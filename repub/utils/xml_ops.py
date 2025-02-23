from xml.dom import minidom, Node

def parse_xml(metafile):
    metafh = open(metafile, 'r', encoding = 'utf8')
    metastr = metafh.read()
    metafh.close()

    xmlnode = minidom.parseString(metastr)
    return xml_to_obj(xmlnode.childNodes[0])
    
def get_node_value(xmlNodes):
    value = []
    ignoreValues = ['\n']
    for node in xmlNodes:
        if node.nodeType == Node.TEXT_NODE:
            if node.data not in ignoreValues:
                value.append(node.data)
    return ''.join(value)

def xml_to_obj(xmlNode):
    xmldict = {}
    for node in xmlNode.childNodes:
        if node.nodeType == Node.ELEMENT_NODE:
           k = node.tagName
           obj = xml_to_obj(node)
           if k in xmldict:
               if not (type(xmldict[k]) == list):
                   xmldict[k] = [xmldict[k]]
               xmldict[k].append(obj)
           else:
               xmldict[k] = obj

    if xmldict:
        return xmldict
    else:
        return get_node_value(xmlNode.childNodes)

