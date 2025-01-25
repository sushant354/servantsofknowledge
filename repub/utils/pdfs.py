import io
import os

import pytesseract
import PyPDF2
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
    outfiles.sort(key = lambda x: x[1])
    pdf_writer = PyPDF2.PdfWriter()
    # export the searchable PDF to searchable.pdf
    for pagenum, outfile in outfiles:
        page = pytesseract.image_to_pdf_or_hocr(outfile, extension='pdf', lang =langs)
        pdf = PyPDF2.PdfReader(io.BytesIO(page))
        pdf_writer.add_page(pdf.pages[0])

    with open(outpdf, "wb") as f:
        pdf_writer.write(f)   
