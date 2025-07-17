from . import htmlproc


class HocrStitch:
    def __init__(self):
        self.head = None
        self.pages = []
        self.DOCTYPE='''<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN"
    "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">'''

    def add_page(self, d):
        if self.head == None:
            self.head = d.find('head')
        pages = d.find_all('div', {'class': 'ocr_page'})
        self.pages.extend(pages)
   
    def get_combined(self):    
        pagenum = 1

        combined = [self.DOCTYPE, '<html>']
        if self.head:
            combined.append('%s' % self.head)
        combined.append('<body>')
        for page in self.pages:
            page['id'] = 'page_%d' % pagenum
            pagenum += 1
            combined.append('%s' % page)
       
        combined.append('</body>')
        combined.append('</html>')
        return '\n'.join(combined)
   
