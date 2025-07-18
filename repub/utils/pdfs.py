import io
import os
import logging
import gzip

import pytesseract
import img2pdf

from pypdf import PdfWriter, PdfReader
from pdf2image import convert_from_path

from . import htmlproc
from .hocrproc import HocrStitch

def pdf_to_images(inpdf, indir):
    images = convert_from_path(inpdf, poppler_path="")

    pagenum = 1
    for image in images:
        filename = ('%d' % pagenum).zfill(4)
        filepath = os.path.join(indir, filename+'.jpg')
        image.save(filepath, 'JPEG')
        pagenum += 1

def get_metadata(filepath):
    reader = PdfReader(filepath)    
    return reader.metadata

def multiple_formats(imgpath, langs, outhocr, outtxt):
    extensions = ['pdf']
    if outhocr:
        extensions.append('hocr')
    if outtxt:    
        extensions.append('txt')

    result = pytesseract.run_and_get_multiple_output(imgpath, lang =langs, \
                                                     extensions = extensions)
    return result

def save_pdf(outfiles, metadata, langs, outpdf, do_ocr, outhocr, outtxt):
    logger = logging.getLogger('repub.utils.pdfs')
    outfiles.sort(key = lambda x: x[0])

    pdf_writer = PdfWriter()
    if metadata:
        pdf_writer.add_metadata(metadata)

    # export the searchable PDF to searchable.pdf
    hocrstitch = HocrStitch()
    head       = None
    textpages  = []

    for pagenum, outfile in outfiles:
        logger.info('Adding page %d with file %s to PDF', pagenum, outfile)
        if do_ocr:
            result = multiple_formats(outfile, langs, outhocr, outtxt)
            page = result[0]
            if outhocr:
                hocr = result[1]
                hocr = hocr.decode('utf-8')
                d = htmlproc.parse_html(hocr)
                hocrstitch.add_page(d)
                if outtxt:    
                    txt = result[2]
                    textpages.append(txt)
            elif outtxt:
                txt = result[1]
                textpages.append(txt)
            else:        
                page = pytesseract.image_to_pdf_or_hocr(outfile, extension='pdf', lang =langs)
        else:
            page = img2pdf.convert(outfile)

        reader = PdfReader(io.BytesIO(page))
        pdf_writer.add_page(reader.get_page(0))
 
    if outtxt:
        txtstr = '\n'.join(textpages)
        f = open(outtxt, 'w', encoding = 'utf-8')
        f.write(txtstr)
        f.close()

    if outhocr:
        hocrstr = hocrstitch.get_combined() 
        hocrbytes = hocrstr.encode('utf-8')
        f = gzip.open(outhocr, 'wb')
        f.write(hocrbytes)
        f.close()

    with open(outpdf, 'wb') as f:
        pdf_writer.write(f)   
