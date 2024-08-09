import re
import uuid

from invenio_indexer.api import RecordIndexer
from invenio_db import db
from invenio_pidstore.models import PersistentIdentifier, PIDStatus
from invenio_pidstore.providers.recordid_v2 import RecordIdProviderV2
from invenio_app.factory import create_app
from flask import current_app

from invenio_search import current_search

from invenio_app_ils.documents.api import DOCUMENT_PID_TYPE, Document
from invenio_app_ils.eitems.api import EITEM_PID_TYPE, EItem
from invenio_app_ils.ill.api import BORROWING_REQUEST_PID_TYPE, BorrowingRequest
from invenio_app_ils.internal_locations.api import INTERNAL_LOCATION_PID_TYPE, InternalLocation
from invenio_app_ils.items.api import ITEM_PID_TYPE, Item
from invenio_app_ils.locations.api import LOCATION_PID_TYPE, Location
from invenio_app_ils.providers.api import PROVIDER_PID_TYPE, Provider
from invenio_app_ils.proxies import current_app_ils
from invenio_app_ils.records_relations.api import RecordRelationsParentChild, RecordRelationsSiblings
from invenio_app_ils.relations.api import Relation
from invenio_app_ils.series.api import SERIES_PID_TYPE, Series
from iso639 import languages
from invenio_pidstore.errors import PIDDoesNotExistError
from invenio_indexer.api import RecordIndexer
from flask import url_for
from invenio_app_ils.literature.covers_builder import build_openlibrary_urls, build_placeholder_urls
def get_languages(langs):
    if isinstance(langs, str):
        return [langs.upper()]

    ls = []
    for lang in langs:
        ls.append(lang.upper())

    return ls

def minter(pid_type, pid_field, record):
    """Mint the given PID for the given record."""
    pid = PersistentIdentifier.create(pid_type, record[pid_field], \
                                     status = PIDStatus.REGISTERED, \
                                     object_type = 'rec', \
                                     object_uuid=record.id)
    return pid                       

def create_pid(self):
    return RecordIdProviderV2.create().pid.pid_value

def build_cover_urls(metadata):
    """Build working ulrs for demo data."""
    cover_metadata = metadata.get("cover_metadata", {})
    is_placeholder = cover_metadata.get('is_placeholder', '')
    if is_placeholder or  'large' not in cover_metadata or 'medium' not in cover_metadata or 'small' not in cover_metadata:
        img   = cover_metadata.get("img", {})
        if img:
            urls = {'is_placeholder': False, 'large': img, \
                    'medium': img, 'small': img}
        else:
            urls = build_placeholder_urls()
    else:    
        urls = {'is_placeholder': False, 'large': cover_metadata['large'], \
           'medium': cover_metadata['medium'], 'small': cover_metadata['small']}
    return urls

def get_library(indexer, name):
    pid,n = re.subn('\s+', '-', name)
    try:
        location = Location.get_record_by_pid(pid, pid_type=LOCATION_PID_TYPE)
    except PIDDoesNotExistError:    
        weekdays = [
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ]
        closed = ["saturday", "sunday"]
        times = [
            {"start_time": "08:00", "end_time": "12:00"},
            {"start_time": "13:00", "end_time": "18:00"},
        ]
        opening_weekdays = []
        for weekday in weekdays:
            is_open = weekday not in closed
            opening_weekdays.append(
                {
                    "weekday": weekday,
                    "is_open": weekday not in closed,
                    **({"times": times} if is_open else {}),
                }
            )

        location = Location.create({'pid': pid, 'name': name, 'opening_weekdays': opening_weekdays})
        minter(LOCATION_PID_TYPE, 'pid', location)
        db.session.commit()
        indexer.index(location)

    return location     
    
def get_internal_location(indexer, location, name):
    pid = name
    try:
        internal = InternalLocation.get_record_by_pid(pid, pid_type=INTERNAL_LOCATION_PID_TYPE)
        return internal
    except PIDDoesNotExistError:    
        pass

    obj = {'pid': pid, 'name': name, 'physical_location': '', 'location_pid': location['pid']}

    internal = InternalLocation.create(obj)

    minter(INTERNAL_LOCATION_PID_TYPE, "pid", internal)
    db.session.commit()
    indexer.index(internal)
    return internal

def get_urls(url_prefix, pid):
    img = '%s/%s.jpg' % (url_prefix, pid)

    return {'is_placeholder': False, 'small': img, 'medium': img, 'large': img}

def get_document(indexer, item):
    item['pid'] = item['identifier']

    try:
        document = Document.get_record_by_pid(item['pid'])
        return document
    except PIDDoesNotExistError:
        pass

    if 'collection' in item:
        item['tags'] = item.pop('collection')

    item['document_type'] = Document.DOCUMENT_TYPES[0]

    if 'language' in item:
        ls = get_languages(item['language'])
        if ls:
            item['languages']  = ls
    if 'description' in item:
        item['abstract'] = item.pop('description')

    year = None
    publisher = None
    if 'publisher' in item or 'date' in item:
        item['imprint'] = {}

        if 'publisher' in item:
            publisher = item.pop('publisher')
            item['imprint']['publisher'] = publisher
        if 'date' in item:
            datestr = item.pop('date')
            item['imprint']['date'] = datestr
            ds = re.findall('\d+', datestr)
            year =  ds[0]

    if 'notes' in item:
        item['note'] = item.pop('notes')

    if publisher:
        item['created_by'] = {'type': 'string', 'value': publisher}
    elif 'creator' in item:    
        item['created_by'] = {'type': 'string', 'value': item['creator']}
        
    if 'creator' in item:
        authors = item.pop('creator')
        if isinstance(authors, str):
            authors = [authors]

        authors = [{'full_name': author} for author in authors]

        item['authors'] = authors


    if 'year' in item:
        year = item.pop('year')
    item['publication_year'] = year 

    if 'subject' in item:
        subjects  = item.pop('subject')
        if isinstance(subjects, str):
            subjects = [subjects]
       
        uniq = set()
        sub  = []
        for x in subjects:
            if x not in uniq:
                uniq.add(x)
                sub.append({'value': x, 'scheme': 'BISAC'})

        item['subjects'] = sub

    document = Document.create(item)
    minter(DOCUMENT_PID_TYPE, 'pid', document)
    db.session.commit()
    indexer.index(document)

    return document

def setup():
    app = create_app()
    indexer = RecordIndexer()
    return app, indexer
    
def get_item(indexer, obj):
    try:
        item = Item.get_record_by_pid(obj['pid'])
        return item
    except PIDDoesNotExistError:
        pass

    item = Item.create(obj)
    minter(ITEM_PID_TYPE, "pid", item)
    db.session.commit()
    indexer.index(item)

    return item


def add_ia_item(indexer, library_name, location, ia_item):
    library    = get_library(indexer, library_name)
    internal = get_internal_location(indexer, library, location)
    document = get_document(indexer, ia_item)

    docid = document['pid']
    created_by = document['created_by']
    item = {'pid': '%s-item' % docid, 'document_pid': docid, \
            'location_pid': library['pid'], 'created_by': created_by, \
            'medium': 'PAPER', 'status': 'CAN_CIRCULATE', 'barcode':'', \
            'internal_location_pid': internal['pid'], \
            'circulation_restriction': 'NO_RESTRICTION'}
    item = get_item(indexer, item)
