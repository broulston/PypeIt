#!/usr/bin/env python
#
# See top-level LICENSE file for Copyright information
#
# -*- coding: utf-8 -*-
"""
This script enables the viewing of a processed FITS file
with extras.  Run above the Science/ folder.
"""

import os
import argparse
import numpy as np

from astropy.table import Table
from astropy.io import fits

from pypeit.core.gui import object_find as gui_object_find
from pypeit import msgs
from pypeit.core.parse import get_dnum


def parser(options=None):

    parser = argparse.ArgumentParser(description='Display sky subtracted, spec2d image in the'
                                                 'interactive object finding GUI.  Run above'
                                                 'the Science/ folder',
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    parser.add_argument('file', type = str, default=None, help='PYPEIT spec2d file')
    parser.add_argument("--list", default=False, help="List the extensions only?",
                        action="store_true")
    parser.add_argument('--det', default=1, type=int, help="Detector")

    return parser.parse_args() if options is None else parser.parse_args(options)


def parse_traces(hdulist_1d, det_nm):
    """Extract the relevant trace information
    """
    traces = []
    for hdu in hdulist_1d:
        if det_nm in hdu.name:
            tbl = Table(hdu.data)
            trace = tbl['TRACE']
            obj_id = hdu.name.split('-')[0]
            traces.append(trace.copy())
    return traces


def main(args):

    # List only?
    hdu = fits.open(args.file)
    head0 = hdu[0].header
    if args.list:
        hdu.info()
        return

    # Init
    sdet = get_dnum(args.det, prefix=False)

    # One detector, sky sub for now
    names = [hdu[i].name for i in range(len(hdu))]

    try:
        exten = names.index('DET{:s}-PROCESSED'.format(sdet))
    except:  # Backwards compatability
        msgs.error('Requested detector {:s} was not processed.\n'
                   'Maybe you chose the wrong one to view?\n'
                   'Set with --det= or check file contents with --list'.format(sdet))
    sciimg = hdu[exten].data
    try:
        exten = names.index('DET{:s}-SKY'.format(sdet))
    except:  # Backwards compatability
        msgs.error('Requested detector {:s} has no sky model.\n'
                   'Maybe you chose the wrong one to view?\n'
                   'Set with --det= or check file contents with --list'.format(sdet))
    skymodel = hdu[exten].data
    try:
        exten = names.index('DET{:s}-MASK'.format(sdet))
    except ValueError:  # Backwards compatability
        msgs.error('Requested detector {:s} has no bit mask.\n'
                   'Maybe you chose the wrong one to view?\n'
                   'Set with --det= or check file contents with --list'.format(sdet))
    mask = hdu[exten].data

    # Object traces
    spec1d_file = args.file.replace('spec2d', 'spec1d')

    det_nm = 'DET{:s}'.format(sdet)
    if os.path.isfile(spec1d_file):
        hdulist_1d = fits.open(spec1d_file)
    else:
        hdulist_1d = []
        msgs.warn('Could not find spec1d file: {:s}'.format(spec1d_file) + msgs.newline() +
                  '                          No objects were extracted.')

    frame = (sciimg - skymodel) * (mask == 0)
    traces = parse_traces(hdulist_1d, det_nm)
    gui_object_find.initialise(frame, None, None, traces=traces, printout=True)
