import logging
import os
import shutil
import argparse
import re

from datetime import datetime

from iarchive import utils
from iarchive import invenio 
from iarchive import xmlops 

def get_mtime(filepath):
    return int(os.path.getmtime(filepath))

def item_to_record(dirname):
    record     = None
    thumbfile  = False
    txtfile    = None

    for filename in os.listdir(dirname):
        if record == None and filename.endswith('_meta.xml'):
            filepath = os.path.join(dirname, filename)
            record = xmlops.xml_to_record(filepath)
            if record:
                record['metats'] = get_mtime(filepath)

        if not thumbfile and re.search('__ia_thumb.jpg$', filename):
            thumbfile = os.path.join(dirname, filename)
        if not txtfile and re.search('_djvu.txt$', filename):
            txtfile = os.path.join(dirname, filename)

    if txtfile and record:
        doctext = open(txtfile, 'r', encoding='utf8', errors = 'ignore').read()
        record['doctext'] = doctext
        record['docts']   = get_mtime(txtfile)

    return record, thumbfile

def get_arg_parser():
    parser = argparse.ArgumentParser(description='For uploading Internet Archive items into InvenioILS')
    parser.add_argument('-L', '--library', dest='libname', action='store',\
                  default='Servants of Knowledge', help='Name of the library')
    parser.add_argument('-l', '--location', dest='location', action='store',\
                  default='GandhiBhavan', help='Location of the library')

    parser.add_argument('-p', '--urlprefix', dest='urlprefix', action='store',\
                  default='/thumbnails', help='URL prefix for thumbnails')
    parser.add_argument('-I', '--iadir', dest='iadir', action='store',\
                  required= True, help='Filepath to IA directory of items')
    
    parser.add_argument('-T', '--thumbdir', dest='thumbdir', action='store',\
                  required= True, help='Website filepath to copy thumbnails')

    return parser

if __name__ == '__main__':
    import sys
    utils.setup_logging('info')

    parser = get_arg_parser()
    args   = parser.parse_args()

    libname  = args.libname
    location = args.location
    iadir    = args.iadir

    topdir   = os.path.basename(iadir)
    thumbdir = os.path.join(args.thumbdir, topdir)
    url_prefix = '%s/%s' % (args.urlprefix, topdir)

    utils.mkdir(thumbdir)

    app, indexer = invenio.setup()
    app.app_context().push()

    logger = logging.getLogger('iarchive')
    for dirname in os.listdir(iadir): 
        dirpath = os.path.join(iadir, dirname)
        if not os.path.isdir(dirpath):
            continue

        record, thumbfile = item_to_record(dirpath)

        if record:
            if 'repub_state' in record and record['repub_state'] not in ['4', '19']:
                logger.warning('Non-indexable repub state %s %s', record['repub_state'], dirname)
                continue
 
            if not thumbfile:
                logger.warning('No thumbfile in %s', dirname)
                continue

            filename = '%s.jpg' % dirname
            record['cover_metadata'] = {'img': '%s/%s' % (url_prefix, filename)}
            invenio.add_ia_item(indexer, libname, args.location, record)
            if thumbfile:
                outfile = os.path.join(thumbdir, filename)
                shutil.copyfile(thumbfile, outfile)
        else:    
            logger.warning('Not able to get record from %s', dirname)
