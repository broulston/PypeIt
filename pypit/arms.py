""" Primary module for guiding the reduction of long/multi-slit data
"""
from __future__ import (print_function, absolute_import, division, unicode_literals)

import yaml
import numpy as np

from astropy import units

from pypit import msgs
from pypit import arparse as settings
from pypit import fluxspec
from pypit import arload
from pypit import armasters
from pypit import armbase
from pypit import arproc
from pypit.core import arprocimg
from pypit import arsave
from pypit import arsciexp
from pypit.core import arsetup
from pypit import arpixels
from pypit.core import arsort
from pypit import artrace
from pypit import arcimage
from pypit import bpmimage
from pypit import biasframe
from pypit import traceslits
from pypit import traceimage

from pypit import ardebug as debugger


def ARMS(fitstbl, setup_dict, reuseMaster=False, reloadMaster=True, sciexp=None):
    """
    Automatic Reduction of Multislit Data

    Parameters
    ----------
    fitsdict : dict
      Contains relevant information from fits header files
    reuseMaster : bool
      If True, a master frame that will be used for another science frame
      will not be regenerated after it is first made.
      This setting comes with a price, and if a large number of science frames are
      being generated, it may be more efficient to simply regenerate the master
      calibrations on the fly.

    Returns
    -------
    status : int
      Status of the reduction procedure
      0 = Successful full execution
      1 = Successful processing of setup or calcheck
    """
    status = 0

    # Generate sciexp list, if need be (it will be soon)
    sv_std_idx = []
    std_dict = {}
    basenames = []  # For fluxing at the very end
    if sciexp is None:
        sciexp = []
        all_sci_ID = fitstbl['sci_ID'].data[fitstbl['science']]  # Binary system: 1,2,4,8, etc.
        for sci_ID in all_sci_ID:
            sciexp.append(arsciexp.ScienceExposure(sci_ID, fitstbl, settings.argflag,
                                                   settings.spect, do_qa=True))
            basenames.append(sciexp[-1]._basename)
            std_idx = arsort.ftype_indices(fitstbl, 'standard', sci_ID)
            if (len(std_idx) > 0):
                if len(std_idx) > 1:
                    msgs.warn("Will only reduce the first, unique standard for each standard frame!")
                if std_idx[0] not in sv_std_idx:  # Only take the first one
                    sv_std_idx.append(std_idx[0])
                    # Standard stars
                    std_dict[std_idx[0]] = arsciexp.ScienceExposure(sci_ID, fitstbl, settings.argflag,
                                                                    settings.spect, do_qa=False, idx_sci=std_idx[0])
    numsci = len(sciexp)

    # Init calib dict
    calib_dict = {}

    # Loop on science exposure
    for sc in range(numsci):

        slf = sciexp[sc]
        sci_ID = slf.sci_ID
        scidx = slf._idx_sci[0]
        msgs.info("Reducing file {0:s}, target {1:s}".format(fitstbl['filename'][scidx], slf._target_name))
        msgs.sciexp = slf  # For QA writing on exit, if nothing else.  Could write Masters too

        #if reloadMaster and (sc > 0):
        #    settings.argflag['reduce']['masters']['reuse'] = True

        # Loop on Detectors
        for kk in range(settings.spect['mosaic']['ndet']):
            det = kk + 1  # Detectors indexed from 1
            if settings.argflag['reduce']['detnum'] is not None:
                if det not in map(int, settings.argflag['reduce']['detnum']):
                    msgs.warn("Skipping detector {:d}".format(det))
                    continue
                else:
                    msgs.warn("Restricting the reduction to detector {:d}".format(det))

            slf.det = det
            dnum = settings.get_dnum(det)
            msgs.info("Working on detector {:s}".format(dnum))

            # Setup
            namp = settings.spect[dnum]["numamplifiers"]
            setup = arsetup.instr_setup(sci_ID, det, fitstbl, setup_dict, namp, must_exist=True)
            settings.argflag['reduce']['masters']['setup'] = setup
            slf.setup = setup

            ###############
            # Get data sections (Could avoid doing this for every sciexp, but it is quick)
            datasec_img = arprocimg.get_datasec_trimmed(fitstbl, det, scidx, settings.argflag, settings.spect)
            #slf._datasec[det-1] = pix_to_amp(naxis0, naxis1, datasec, numamplifiers)

            # Calib dict
            if setup not in calib_dict.keys():
                calib_dict[setup] = {}

            # TODO -- Update/avoid the following with new settings
            tsettings = settings.argflag.copy()
            tsettings['detector'] = settings.spect[settings.get_dnum(det)]
            try:
                tsettings['detector']['dataext'] = tsettings['detector']['dataext01']  # Kludge; goofy named key
            except KeyError: # LRIS, DEIMOS
                tsettings['detector']['dataext'] = None
            tsettings['detector']['dispaxis'] = settings.argflag['trace']['dispersion']['direction']

            ###############
            # Prepare for Bias subtraction
            #   bias will either be an image (ndarray) or a command (str, e.g. 'overscan') or none
            if 'bias' in calib_dict[setup].keys():
                msbias = calib_dict[setup]['bias']
            else:
                # Init
                bias = biasframe.BiasFrame(settings=tsettings, setup=setup, det=det, fitstbl=fitstbl, sci_ID=sci_ID)
                # Grab/build the MasterFrame (ndarray or str)
                #   If an image is generated, it will be saved to disk a a MasterFrame
                msbias = bias.master()
                # Save
                calib_dict[setup]['bias'] = msbias

            ###############
            # Generate a master arc frame
            if 'arc' in calib_dict[setup].keys():
                msarc = calib_dict[setup]['arc']
            else:
                AImage = arcimage.ArcImage([], spectrograph=settings.argflag['run']['spectrograph'],
                                           settings=tsettings, det=det, setup=setup, sci_ID=sci_ID,
                                           msbias=msbias, fitstbl=fitstbl)
                msarc = AImage.master()
                # Save
                calib_dict[setup]['arc'] = msarc

            ###############
            # Generate a bad pixel mask (should not repeat)
            if 'bpm' in calib_dict[setup].keys():
                msbpm = calib_dict[setup]['bpm']
            else:
                bpmImage = bpmimage.BPMImage(spectrograph=settings.argflag['run']['spectrograph'],
                                             settings=tsettings, det=det,
                                             shape=msarc.shape,
                                             binning=fitstbl['binning'][scidx],
                                             reduce_badpix=settings.argflag['reduce']['badpix'],
                                             msbias=msbias)
                msbpm = bpmImage.build()
                # Save
                calib_dict[setup]['bpm'] = msbpm

            ###############
            # Generate an array that provides the physical pixel locations on the detector
            pixlocn = arpixels.gen_pixloc(msarc.shape, det, settings.argflag)
            # TODO -- Deprecate using slf for this
            slf.SetFrame(slf._pixlocn, pixlocn, det)

            ###############
            # Slit Tracing
            if 'trace' in calib_dict[setup].keys():  # Internal
                Tslits = calib_dict[setup]['trace']
            else:
                # Load master frame?
                if (settings.argflag['reduce']['masters']['reuse']) or (settings.argflag['reduce']['masters']['force']):
                    mstrace_name = armasters.master_name('trace', setup)
                    Tslits = traceslits.TraceSlits.from_master_files(mstrace_name, load_pix_obj=True)  # Returns None if none exists
                else:
                    Tslits = None

                # Build it?  Beginning with the trace image
                if Tslits is None:
                    # Build the trace image first
                    trace_image_files = arsort.list_of_files(fitstbl, 'trace', sci_ID)
                    Timage = traceimage.TraceImage(trace_image_files,
                                                        spectrograph=settings.argflag['run']['spectrograph'],
                                                        settings=tsettings, det=det)
                    mstrace = Timage.process(bias_subtract=msbias, trim=settings.argflag['reduce']['trim'])

                    # Setup up the settings (will be Refactored with settings)
                    tmp = dict(trace=settings.argflag['trace'], masters=settings.argflag['reduce']['masters'])
                    tmp['masters']['directory'] = settings.argflag['run']['directory']['master']+'_'+ settings.argflag['run']['spectrograph']

                    # Now trace me some slits
                    Tslits = traceslits.TraceSlits(mstrace, slf._pixlocn[det-1], det=det, settings=tmp,
                                                   binbpx=msbpm, setup=setup)
                    _ = Tslits.run(armlsd=True)
                    # QA
                    Tslits._qa()
                    # Save to disk
                    Tslits.save_master()
                # Save in calib
                calib_dict[setup]['trace'] = Tslits

            # Save in slf
            # TODO -- Deprecate this means of holding the info (e.g. just pass around Tslits)
            slf.SetFrame(slf._lordloc, Tslits.lcen, det)
            slf.SetFrame(slf._rordloc, Tslits.rcen, det)
            slf.SetFrame(slf._pixcen, Tslits.pixcen, det)
            slf.SetFrame(slf._pixwid, Tslits.pixwid, det)
            slf.SetFrame(slf._lordpix, Tslits.lordpix, det)
            slf.SetFrame(slf._rordpix, Tslits.rordpix, det)
            slf.SetFrame(slf._slitpix, Tslits.slitpix, det)

            # Initialize maskslit
            slf._maskslits[det-1] = np.zeros(slf._lordloc[det-1].shape[1], dtype=bool)

            ###############
            # Generate the 1D wavelength solution
            update = slf.MasterWaveCalib(fitstbl, det, msarc)
            if update and reuseMaster:
                armbase.UpdateMasters(sciexp, sc, det, ftype="arc", chktype="trace")

            ###############
            # Derive the spectral tilt
            if slf._tilts[det-1] is None:
                tilts = armasters.load_master_frame(slf, "tilts")
                if tilts is None:
                    # First time tilts are derived for this arc frame --> derive the order tilts
                    tilts, satmask, outpar = artrace.multislit_tilt(slf, msarc, det)
                    slf.SetFrame(slf._tilts, tilts, det)
                    slf.SetFrame(slf._satmask, satmask, det)
                    msgs.bug("This outpar is only the last slit!!  JXP doesn't think it matters for now")
                    slf.SetFrame(slf._tiltpar, outpar, det)
                    armasters.save_masters(slf, det, mftype='tilts')
                else:
                    slf.SetFrame(slf._tilts, tilts, det)

            ###############
            # Prepare the pixel flat field frame
            update = slf.MasterFlatField(fitstbl, det, msbias, datasec_img)
            if update and reuseMaster: armbase.UpdateMasters(sciexp, sc, det, ftype="flat", chktype="pixelflat")

            ###############
            # Generate/load a master wave frame
            update = slf.MasterWave(fitstbl, sc, det)
            if update and reuseMaster:
                armbase.UpdateMasters(sciexp, sc, det, ftype="arc", chktype="wave")

            ###############
            # Check if the user only wants to prepare the calibrations only
            #msgs.info("All calibration frames have been prepared")
            #if settings.argflag['run']['preponly']:
            #    msgs.info("If you would like to continue with the reduction, disable the command:" + msgs.newline() +
            #              "run preponly False")
            #    continue

            ###############
            # Write setup
            #setup = arsort.calib_setup(sc, det, fitsdict, setup_dict, write=True)
            # Write MasterFrames (currently per detector)
            #armasters.save_masters(slf, det, setup)

            ###############
            # Load the science frame and from this generate a Poisson error frame
            msgs.info("Loading science frame")
            sciframe = arload.load_frames(fitstbl, [scidx], det,
                                          frametype='science',
                                          msbias=msbias) # slf._msbias[det-1])
            sciframe = sciframe[:, :, 0]
            # Extract
            msgs.info("Processing science frame")
            arproc.reduce_multislit(slf, sciframe, msbpm, datasec_img, scidx, fitstbl, det)


            ######################################################
            # Reduce standard here; only legit if the mask is the same
            std_idx = arsort.ftype_indices(fitstbl, 'standard', sci_ID)
            if len(std_idx) > 0:
                std_idx = std_idx[0]
            else:
                continue
            stdslf = std_dict[std_idx]
            if stdslf.extracted[det-1] is False:
                # Fill up the necessary pieces
                for iattr in ['pixlocn', 'lordloc', 'rordloc', 'pixcen', 'pixwid', 'lordpix', 'rordpix',
                              'slitpix', 'tilts', 'satmask', 'maskslits', 'slitprof',
                              'mspixelflatnrm', 'mswave']:
                    setattr(stdslf, '_'+iattr, getattr(slf, '_'+iattr))  # Brings along all the detectors, but that is ok
                # Load
                stdframe = arload.load_frames(fitstbl, [std_idx], det, frametype='standard', msbias=msbias)
                stdframe = stdframe[:, :, 0]
                # Reduce
                msgs.info("Processing standard frame")
                arproc.reduce_multislit(stdslf, stdframe, msbpm, datasec_img, std_idx, fitstbl, det, standard=True)
                # Finish
                stdslf.extracted[det-1] = True

        ###########################
        # Write
        # Write 1D spectra
        save_format = 'fits'
        if save_format == 'fits':
            outfile = settings.argflag['run']['directory']['science']+'/spec1d_{:s}.fits'.format(slf._basename)
            helio_dict = dict(refframe=settings.argflag['reduce']['calibrate']['refframe'],
                              vel_correction=slf.vel_correction)
            arsave.save_1d_spectra_fits(slf._specobjs, fitstbl[slf._idx_sci[0]], outfile,
                                            helio_dict=helio_dict, obs_dict=settings.spect['mosaic'])
            #arsave.save_1d_spectra_fits(slf, fitstbl)
        elif save_format == 'hdf5':
            arsave.save_1d_spectra_hdf5(slf)
        else:
            msgs.error(save_format + ' is not a recognized output format!')
        arsave.save_obj_info(slf, fitstbl)
        # Write 2D images for the Science Frame
        arsave.save_2d_images(slf, fitstbl)
        # Free up some memory by replacing the reduced ScienceExposure class
        sciexp[sc] = None

    # Write standard stars
    for key in std_dict.keys():
        outfile = settings.argflag['run']['directory']['science']+'/spec1d_{:s}.fits'.format(std_dict[key]._basename)
        arsave.save_1d_spectra_fits(std_dict[key]._specobjs, fitstbl[std_idx], outfile,
                                        obs_dict=settings.spect['mosaic'])

    #########################
    # Flux towards the very end..
    #########################
    if settings.argflag['reduce']['calibrate']['flux'] and (len(std_dict) > 0):
        # Standard star (is this a calibration, e.g. goes above?)
        msgs.info("Processing standard star")
        msgs.info("Taking one star per detector mosaic")
        msgs.info("Waited until very end to work on it")
        msgs.warn("You should probably consider using the pypit_flux_spec script anyhow...")

        # Kludge settings
        fsettings = settings.spect.copy()
        fsettings['run'] = settings.argflag['run']
        fsettings['reduce'] = settings.argflag['reduce']
        # Generate?
        if (settings.argflag['reduce']['calibrate']['sensfunc']['archival'] == 'None'):
            std_keys = list(std_dict.keys())
            std_key = std_keys[0] # Take the first extraction
            FxSpec = fluxspec.FluxSpec(settings=fsettings, std_specobjs=std_dict[std_key]._specobjs,
                                       setup=setup)  # This takes the last setup run, which is as sensible as any..
            sensfunc = FxSpec.master(fitstbl[std_key])
        else:  # Input by user
            FxSpec = fluxspec.FluxSpec(settings=fsettings,
                                       sens_file=settings.argflag['reduce']['calibrate']['sensfunc']['archival'])
            sensfunc = FxSpec.sensfunc
        # Flux
        msgs.info("Fluxing with {:s}".format(sensfunc['std']['name']))
        for kk, sci_ID in enumerate(all_sci_ID):
            # Load from disk (we zero'd out the class to free memory)
            if save_format == 'fits':
                sci_spec1d_file = settings.argflag['run']['directory']['science']+'/spec1d_{:s}.fits'.format(
                    basenames[kk])
            # Load
            sci_specobjs, sci_header = arload.load_specobj(sci_spec1d_file)
            FxSpec.sci_specobjs = sci_specobjs
            FxSpec.sci_header = sci_header
            # Flux
            FxSpec.flux_science()
            # Over-write
            FxSpec.write_science(sci_spec1d_file)

    return status