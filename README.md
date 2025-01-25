For REPUB project
==================
First install the dependencies ..

symlink repub in your site packages
```
ln -s <maindirectory>/servantsofknowledge/repub <python_site_package_dir/repub

```
Install dependencies
```
pip install -r requirements.txt 
```

process_raw.py is the main program to transform scanned images to a PDF with text layer

To convert Internet Archive images to PDF with text layer with cropping and language "eng+asm"
```
python process_raw.py -i <ia_scan_dir> -O <output_file_path.pdf> -c -L eng+asm
```
To convert a PDF file to a PDF file with text layer with cropping and language "eng+kan". Remove "-c" if no need to crop.
```
python process_raw.py -I <input_file_path.pdf> -O <output_file_path.pdf> -c -L eng+kan
```

Options
```
python process_raw.py -h
usage: process_raw.py [-h] [-i INDIR] [-I INPDF] [-o OUTDIR] [-O OUTPDF]
                      [-l LOGLEVEL] [-L LANGS] [-f LOGFILE] [-m MAXCONTOURS]
                      [-x XMAX] [-y YMAX] [-d] [-p [PAGENUMS ...]] [-g] [-c]

For processing scanned book pages

options:
  -h, --help            show this help message and exit
  -i INDIR, --indir INDIR
                        Filepath to scanned images directory
  -I INPDF, --inpdf INPDF
                        Input PDF File
  -o OUTDIR, --outdir OUTDIR
                        Filepath to processed directory
  -O OUTPDF, --outpdf OUTPDF
                        Output PDF filepath
  -l LOGLEVEL, --loglevel LOGLEVEL
                        debug level
  -L LANGS, --language LANGS
                        language for tesseract
  -f LOGFILE, --logfile LOGFILE
                        log file
  -m MAXCONTOURS, --maxcontours MAXCONTOURS
                        max number of contours to be examined
  -x XMAX, --xmax XMAX  horizontal line limits in pixels
  -y YMAX, --ymax YMAX  vertical line limits in pixels
  -d, --drawcontours    vertical line limits in pixels
  -p [PAGENUMS ...], --pagenums [PAGENUMS ...]
                        pagenums that should only be processed
  -g, --gray            only gray the image and threshold it
  -c, --crop            crop the scanned image

```
