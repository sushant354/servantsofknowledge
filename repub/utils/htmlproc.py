from bs4 import BeautifulSoup

def parse_html(webpage, parser = 'html.parser'):
    d = BeautifulSoup(webpage, parser)
    #try:
    #except Exception as e:
    #    d = None
    return d        
