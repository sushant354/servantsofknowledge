For REPUB project
==================
First install the dependencies ..

symlink repub in your site packages
```
ln -s /usr/lib/python3.12/repub <maindirectory>/servantsofknowledge/repub

```
process_raw.py in tools directory is the main program to transform scanned images to a PDF with text layer


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
