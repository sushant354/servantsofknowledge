import logging

#logformat   = '%(asctime)s: %(name)s: %(levelname)s %(message)s'
logformat   = '%(name)s: %(message)s'
dateformat  = '%Y-%m-%d %H:%M:%S'

def initialize_file_logging(loglevel, filepath):
    logging.basicConfig(\
        level    = loglevel,  \
        format   = logformat, \
        datefmt  = dateformat, \
        stream   = filepath
    )

def initialize_stream_logging(loglevel = logging.INFO):
    logging.basicConfig(\
        level    = loglevel,  \
        format   = logformat, \
        datefmt  = dateformat \
    )


def setup_logging(level, filename = None):
    leveldict = {'critical': logging.CRITICAL, 'error': logging.ERROR, \
                 'warning': logging.WARNING,   'info': logging.INFO, \
                 'debug': logging.DEBUG}
    loglevel = leveldict[level]

    if filename:
        filestream = open(filename, 'w', encoding='utf8')
        initialize_file_logging(loglevel, filestream)
    else:
        initialize_stream_logging(loglevel)

