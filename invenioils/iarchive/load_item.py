import xml
import logging
import os

from iarchive import utils
from iarchive import invenio 
from iarchive import xmlops 

def item_to_record(dirname):
    for filename in os.listdir(dirname):
        if filename.endswith('_meta.xml'):
            filepath = os.path.join(dirname, filename)
            return xmlops.xml_to_record(filepath)
    return None        


if __name__ == '__main__':
    import sys
    utils.setup_logging('info')
    itemdir = sys.argv[1]

    item = item_to_record(itemdir)
    if item == None:
        sys.exit(0)
    
    app, indexer = invenio.setup()
    app.app_context().push()
    invenio.add_ia_item(indexer, 'Servants of Knowledge', item)
