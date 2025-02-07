import io
import os
import logging

import pytesseract
from pypdf import PdfWriter, PdfReader
from pdf2image import convert_from_path

def pdf_to_images(inpdf, indir):
    images = convert_from_path(inpdf, poppler_path="")

    pagenum = 1
    for image in images:
        filename = ('%d' % pagenum).zfill(4)
        filepath = os.path.join(indir, filename+'.jpg')
        image.save(filepath, 'JPEG')
        pagenum += 1

def save_pdf(outfiles, langs, outpdf):
    logger = logging.getLogger('repub.utils.pdfs')
    outfiles.sort(key = lambda x: x[0])
    pdf_writer = PdfWriter()
    # export the searchable PDF to searchable.pdf
    for pagenum, outfile in outfiles:
        logger.info('Adding page %d with file %s to PDF', pagenum, outfile)
        page = pytesseract.image_to_pdf_or_hocr(outfile, extension='pdf', lang =langs)
        reader = PdfReader(io.BytesIO(page))
        pdf_writer.add_page(reader.get_page(0))

    with open(outpdf, "wb") as f:
        pdf_writer.write(f)   
