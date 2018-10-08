""" Module for I/O in arclines
"""
from __future__ import (print_function, absolute_import, division, unicode_literals)

import numpy as np
import os
import datetime

from pkg_resources import resource_filename

from astropy.table import Table, Column, vstack
from astropy.io import fits

from linetools import utils as ltu

from pypeit import msgs

import pypeit  # For path
from pypeit.core.wavecal import defs

line_path = resource_filename('pypeit', '/data/arc_lines/lists/')
nist_path = resource_filename('pypeit','/data/arc_lines/NIST/')


def load_by_hand():
    """ By-hand line list
    Parameters
    ----------
    line_file
    add_path

    Returns
    -------
    byhand : Table

    """
    str_len_dict = defs.str_len()

    src_file = resource_filename('pypeit', '/data/arc_lines/sources/by_hand_list.ascii')
    # Read
    line_list = Table.read(src_file, format='ascii.fixed_width', comment='#')
    # Add
    line_list['NIST'] = 1
    # Deal with Instr and Source
    ilist, slist = [], []
    for row in line_list:
        ilist.append(defs.instruments()[row['sInstr']])  # May need to split
        slist.append(row['sSource'])
    line_list['Instr'] = ilist
    line_list['Source'] = np.array(slist, dtype='S{:d}'.format(str_len_dict['Source']))
    # Trim
    return line_list[['ion', 'wave', 'NIST', 'Instr', 'amplitude', 'Source']]


def load_line_list(line_file, add_path=False, use_ion=False, NIST=False):
    """
    Parameters
    ----------
    line_file : str
      Full path to line_list or name of ion
    add_path : bool, optional
      Not yet implemented
    NIST : bool, optional
      NIST formatted table?
    use_ion : bool, optional
      Interpret line_file as an ion, e.g. CuI

    Returns
    -------
    line_list : Table

    """
    if NIST:
        path = nist_path
    else:
        path = line_path
    if use_ion:
        if NIST:
            line_file = path+'{:s}_vacuum.ascii'.format(line_file)
        else:
            line_file = path+'{:s}_lines.dat'.format(line_file)
    line_list = Table.read(line_file, format='ascii.fixed_width', comment='#')
    #  NIST?
    if NIST:
        # Remove unwanted columns
        tkeys = line_list.keys()
        for badkey in ['Ritz','Acc.','Type','Ei','Lower','Upper','TP','Line']:
            for tkey in tkeys:
                if badkey in tkey:
                    line_list.remove_column(tkey)
        # Relative intensity -- Strip junk off the end
        reli = []
        for imsk, idat in zip(line_list['Rel.'].mask, line_list['Rel.'].data):
            if imsk:
                reli.append(0.)
            else:
                try:
                    reli.append(float(idat))
                except ValueError:
                    try:
                        reli.append(float(idat[:-1]))
                    except ValueError:
                        reli.append(0.)
        line_list.remove_column('Rel.')
        line_list['RelInt'] = reli
        #
        gdrows = line_list['Observed'] > 0.  # Eliminate dummy lines
        line_list = line_list[gdrows]
        line_list.rename_column('Observed','wave')
        # Others
        # Grab ion name
        i0 = line_file.rfind('/')
        i1 = line_file.rfind('_')
        ion = line_file[i0+1:i1]
        line_list.add_column(Column([ion]*len(line_list), name='Ion', dtype='U5'))
        line_list.add_column(Column([1]*len(line_list), name='NIST'))

    # Return
    return line_list


def load_line_lists(lines, unknown=False, skip=False, all=False, NIST=False):
    """ Loads a series of line list files

    Parameters
    ----------
    lamps : list
    unknown : bool, optional
    skip : bool, optional
      Skip missing line lists (mainly for building)
    NIST : bool, optional
      Load the full NIST linelists

    Returns
    -------
    line_list : Table

    """
    import glob

    # All?
    if all:
        line_files = glob.glob(line_path+'*_lines.dat')
        lines = []
        for line_file in line_files:
            i0 = line_file.rfind('/')
            i1 = line_file.rfind('_')
            lines.append(line_file[i0+1:i1])

    # Read standard files
    lists = []
    for line in lines:
        if NIST:
            line_file = nist_path+'{:s}_vacuum.ascii'.format(line)
        else:
            line_file = line_path+'{:s}_lines.dat'.format(line)
        if not os.path.isfile(line_file):
            if not skip:
                import pdb; pdb.set_trace()
                raise IOError("Input line {:s} is not included in arclines".format(line))
        else:
            lists.append(load_line_list(line_file, NIST=NIST))
    # Stack
    if len(lists) == 0:
        return None
    line_lists = vstack(lists, join_type='exact')

    # Unknown
    if unknown:
        unkn_lines = load_unknown_list(lines)
        unkn_lines.remove_column('line_flag')  # may wish to have this info
        # Stack
        line_lists = vstack([line_lists, unkn_lines])

    # Return
    return line_lists


def load_source_table():
    """ Load table of arcline sources

    Returns
    -------
    sources : Table

    """
    src_file = pypeit.__path__[0]+'/data/arc_lines/sources/arcline_sources.ascii'
    # Load
    sources = Table.read(src_file, format='ascii.fixed_width', comment='#')
    # Return
    return sources


def load_tree(polygon=4, numsearch=20):
    """ Load a KDTree of ThAr patterns that is stored on disk

    Parameters
    ----------
    polygon : int
      Number of sides to the polygon used in pattern matching:
        polygon=3  -->  trigon (two anchor lines and one floating line)
        polygon=4  -->  tetragon (two anchor lines and two floating lines)
        polygon=5  -->  pentagon (two anchor lines and three floating lines)
        ...
    numsearch : int
      Number of consecutive detected lines used to generate a pattern. For
      example, if numsearch is 4, then for a trigon, the following patterns will
      be generated (assuming line #1 is the left anchor):
      1 2 3  (in this case line #3 is the right anchor)
      1 2 4  (in this case line #4 is the right anchor)
      1 3 4  (in this case line #4 is the right anchor)

    Returns
    -------
    file_load : KDTree instance
      The KDTree containing the patterns
    index : ndarray
      For each pattern in the KDTree, this array stores the corresponding index in
      the linelist
    """

    import pickle
    filename = pypeit.__path__[0] +\
               '/data/arc_lines/lists/ThAr_patterns_poly{0:d}_search{1:d}.kdtree'.format(polygon, numsearch)
    fileindx = pypeit.__path__[0] +\
               '/data/arc_lines/lists/ThAr_patterns_poly{0:d}_search{1:d}.index.npy'.format(polygon, numsearch)
    try:
        file_load = pickle.load(open(filename, 'rb'))
        index = np.load(fileindx)
    except FileNotFoundError:
        msgs.info('The requested KDTree was not found on disk' + msgs.newline() +
                  'please be patient while the ThAr KDTree is built and saved to disk.')
        from pypeit.core.wavecal import kdtree_generator
        file_load, index = kdtree_generator.main(polygon, numsearch=numsearch, verbose=True,
                                                 ret_treeindx=True, outname=filename)

    return file_load, index


def load_nist(ion):
    """Parse a NIST ASCII table.  Note that the long ---- should have
    been commented out and also the few lines at the start.

    Parameters
    ----------
    ion : str
      Name of ion
    Returns
    -------
    tbl : Table
      Table of lines
    """
    import glob
    # Root (for development only)
    root = pypeit.__path__[0]
    # Find file
    srch_file = root + '/data/arc_lines/NIST/'+ion+'_vacuum.ascii'
    nist_file = glob.glob(srch_file)
    if len(nist_file) == 0:
        raise IOError("Cannot find NIST file {:s}".format(srch_file))
    # Read
    nist_tbl = Table.read(nist_file[0], format='ascii.fixed_width')
    gdrow = nist_tbl['Observed'] > 0.  # Eliminate dummy lines
    nist_tbl = nist_tbl[gdrow]
    # Now unique values only (no duplicates)
    uniq, indices = np.unique(nist_tbl['Observed'],return_index=True)
    nist_tbl = nist_tbl[indices]
    # Deal with Rel
    agdrel = []
    for row in nist_tbl:
        try:
            gdrel = int(row['Rel.'])
        except:
            try:
                gdrel = int(row['Rel.'][:-1])
            except:
                gdrel = 0
        agdrel.append(gdrel)
    agdrel = np.array(agdrel)
    # Remove and add
    nist_tbl.remove_column('Rel.')
    nist_tbl.remove_column('Ritz')
    nist_tbl['RelInt'] = agdrel
    #nist_tbl.add_column(Column([ion]*len(nist_tbl), name='Ion', dtype='S5'))
    nist_tbl.add_column(Column([ion]*len(nist_tbl), name='Ion', dtype='U5'))
    nist_tbl.rename_column('Observed','wave')
    # Return
    return nist_tbl


def load_unknown_list(lines, unknwn_file=None, all=False):
    """
    Parameters
    ----------
    lines : list
      Restricted lines;  use all=True for all
    unknwn_file : str, optional
    all : bool, optional


    Returns
    -------
    unknwn_lines : Table

    """
    line_dict = defs.lines()
    # Load
    line_path = pypeit.__path__[0]+'/data/arc_lines/lists/'
    if unknwn_file is None:
        unknwn_file = line_path+'UNKNWNs.dat'
    line_list = load_line_list(unknwn_file)
    # Cut on input lamps?
    if all:
        return line_list
    else:
        msk = np.array([False]*len(line_list))
        for line in lines:
            line_flag = line_dict[line]
            match = line_list['line_flag'] % (2*line_flag) >= line_flag
            msk[match] = True
        # Finish
        return line_list[msk]


def load_spectrum(spec_file, index=0):
    """ Load a simple spectrum from input file

    Parameters
    ----------
    spec_file : str
      .fits --  Assumes simple ndarray in 0 extension
      .ascii -- Assumes Table.read(format='ascii') will work with single column

    Returns
    -------

    """
    import h5py
    iext = spec_file.rfind('.')
    if 'ascii' in spec_file[iext:]:
        tbl = Table.read(spec_file, format='ascii')
        key = tbl.keys()[0]
        spec = tbl[key].data
    elif 'fits' in spec_file[iext:]:
        spec = fits.open(spec_file)[0].data
    elif 'hdf5' in spec_file[iext:]:
        hdf = h5py.File(spec_file, 'r')
        if 'arcs' in hdf.keys():
            print("Taking arc={:d} in this file".format(index))
            spec = hdf['arcs/'+str(index)+'/spec'].value
        else:
            raise IOError("Not ready for this hdf5 file")
    elif 'json' in spec_file[iext:]:
        jdict = ltu.loadjson(spec_file)
        try:
            spec = np.array(jdict['spec'])
        except KeyError:
            raise IOError("spec not in your JSON dict")
    # Return
    return spec


def write_line_list(tbl, outfile):
    """
    Parameters
    ----------
    tbl
    outfile
    """
    # Format
    tbl['wave'].format = '10.4f'
    # Write
    with open(outfile,'w') as f:
        f.write('# Creation Date: {:s}\n'.format(str(datetime.date.today().strftime('%Y-%b-%d'))))
        tbl.write(f, format='ascii.fixed_width')
