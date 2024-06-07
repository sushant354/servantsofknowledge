from xml.dom import minidom, Node

def xml_to_obj(xmlNode):
    xmldict = {} 
    for node in xmlNode.childNodes:
        if node.nodeType == Node.ELEMENT_NODE:
           
           k = node.tagName
           if k == 'description':
               xmldict[k] = ''.join([x.toxml() for x in node.childNodes])
               continue

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

def get_node_value(xmlNodes):
    value = [] 
    ignoreValues = ['\n']
    for node in xmlNodes:
        if node.nodeType == Node.TEXT_NODE:
            if node.data not in ignoreValues:
                value.append(node.data)
    return ''.join(value)

def xml_to_record(filepath):
    xmlnode = minidom.parse(filepath)
    return xml_to_obj(xmlnode.childNodes[0])

if __name__ == '__main__':
    import sys
    obj = xml_to_record(sys.argv[1])

    print (obj)
