def find_vertical_horizontal_lines(img):    
    rows, columns = img.shape
    lines = []
    for i in range(rows):
        rowlines = []
        for j in range(columns):
            gray = img[i, j]
            point = (i, j)
            if gray >= 1:
                if j > 0 and rowlines[j-1] != None:
                    if isinstance(rowlines[j-1], list):
                        lastp =  j-1
                    else:
                        _, lastp = rowlines[j-1]

                    rowlines[lastp].append(point)
                    rowlines.append((i, lastp))
                else:
                    rowlines.append([point])
            else:
                rowlines.append(None)

        lines.append(rowlines)       


    while 1:
       merged = merge_lines(lines, rows, columns)
       if merged < 5:
           break
    
    count = []
    for i in range(rows):
        for j in range(columns):
            if isinstance(lines[i][j], list):
                count.append((i, j, len(lines[i][j])))
    count.sort(key = lambda tup: tup[2], reverse = True)                        

    for t in count:
       print (t)
            
    return img

def merge_lines(lines, rows, columns):
    count = 0
    for i in range(rows):
        for j in range(columns):
            if isinstance(lines[i][j], list):
                num = len(lines[i][j])
                uniq = set()
                uniq.add(i)
                for l, m in lines[i][j]:
                    if l not in uniq:
                        uniq.add(l)
                tryrows = list(uniq)
                tryrows.sort()
                m = tryrows[0]
                if m > 0:
                    tryrows.append(m -1)

                m = tryrows[-1]
                if m < rows-1:
                    tryrows.append(m +1)


                for l in tryrows:
                    if j+num < columns and isinstance(lines[l][j+num], list):
                        merge(lines, i, j, l, j+num)
                        count += 1
    return count 

def merge(lines, i, j, l, m):
    lines[i][j].extend(lines[l][m])
    lastp = (i, j)
    for px, py in lines[l][m]:
        lines[px][py] = lastp
    lines[l][m] = lastp

