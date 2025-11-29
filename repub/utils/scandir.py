import cv2
import os
import json
import re
import logging

from repub.utils import xml_ops

def read_image(scaninfo, infile):
    img = cv2.imread(infile)

    if scaninfo:
        if scaninfo['rotateDegree'] == -90:
            img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
        elif scaninfo['rotateDegree'] == 90:
            img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)

    return img    

def get_scandata(indir):
    filepath = os.path.join(indir, 'scandata.json')
    if not os.path.exists(filepath):
        return None

    scanfh = open(filepath, 'r', encoding = 'utf8')
    s      = scanfh.read()
    scanfh.close()
    return json.loads(s)

def read_metadata(indir):
    filepath = os.path.join(indir, 'metadata.xml')
    if not os.path.exists(filepath):
        return None

    metadata = xml_ops.parse_xml(filepath)
    return metadata

def get_metadata(indir):
    metadata = read_metadata(indir)
    m = {}
    if  metadata is not None:
        for k, v in metadata.items():
            m['/%s' % k.title()] = v

    # Read identifier from identifier.txt
    identifier_path = os.path.join(indir, 'identifier.txt')
    if os.path.exists(identifier_path):
        with open(identifier_path, 'r', encoding='utf8') as identifier_fh:
            identifier = identifier_fh.read().strip()
            if identifier:
                m['/Identifier'] = identifier
    return m    

def get_scanned_pages(pagedata, indir, outdir, pagenums, logger=None):
    if logger is None:
        logger = logging.getLogger('repub.scandir')
    fnames = []
    for filename in os.listdir(indir):
        reobj = re.match('(?P<pagenum>\\d{4}).(jpg|jp2)$', filename)
        if reobj:
            groupdict = reobj.groupdict('pagenum')
            pagenum   = groupdict['pagenum']

            fnames.append((filename, pagenum))

    fnames.sort(key= lambda x:x[1])

    for filename, pagenum in fnames:
        infile  = os.path.join(indir, filename)
        if re.search('.jp2$', filename):
            outfile = os.path.join(outdir, '%s.jpg' % pagenum)
        else:
            outfile = os.path.join(outdir, filename)

        pageinfo  = None
        pagenum   = int(pagenum)
        if pagedata:
            pageinfo = pagedata['%d' % pagenum]

        if (not pageinfo or pageinfo['pageType'] != 'Color Card') and \
                (not pagenums or pagenum in pagenums):
            logger.error ('FILENAME: %s', filename)
            img = read_image(pageinfo, infile) 
            yield (img, infile, outfile, pagenum)

class Scandir:
    def __init__(self, indir, outdir, pagenums, logger=None):
        if logger is None:
            self.logger = logging.getLogger('repub.scandir')
        else:
            self.logger = logger
        self.indir    = self.find_input_dir(indir)
        self.outdir   = outdir
        self.pagenums = pagenums
       
        scandata = get_scandata(self.indir)

        self.pagedata = None
        if scandata:
            self.pagedata = scandata['pageData']
        self.metadata =  get_metadata(self.indir)

    def find_input_dir(self, indir):
        while True:
            filenames = os.listdir(indir)
            if len(filenames) != 1:
                return indir
            
            filepath = os.path.join(indir, filenames[0])
            if not os.path.isdir(filepath):
                return indir
            indir = filepath

        return None

    def get_scanned_pages(self):
        for d in get_scanned_pages(self.pagedata, self.indir, self.outdir, \
                                   self.pagenums, self.logger):
            yield d

    def is_cover_page(self, pagenum):
        if not self.pagedata:
            if pagenum == 1:
                return True
            else: 
                return False 

        pageinfo = self.pagedata['%d' % pagenum]

        cover = False
        if pageinfo and pageinfo['pageType'] == 'Cover':
            cover = True

        return cover    

if __name__ == '__main__':
    scandir = Scandir('/home/sushant/servantsofknowledge/repub/repubui/media/uploads/fd6686e1-cfec-4099-9039-43f765e5439c/extracted/Book', 'Book', None)        
    print (scandir.metadata)
