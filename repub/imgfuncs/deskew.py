import cv2
import math
import numpy as np
import logging

from .utils import  find_contour, get_hvlines

def deskew(img, xmax, ymax, maxcontours):
    logger = logging.getLogger('repub.deskew')
    contours = find_contour(img)

    contours = contours[:maxcontours]

    hlines, vlines = get_hvlines(contours, xmax, ymax)

    hangle = get_lines_angle(hlines)
    logger.warning('Hangle: %s', hangle)
    vangle = get_lines_angle(vlines)
    logger.warning('Vangle: %s', vangle)

    angle_deg = None 
    if hangle and abs(hangle) > 0:
        angle_deg = hangle 
        img = rotate(img, angle_deg)
    return img, angle_deg 

def rotate(img, angle_deg):
    height, width = img.shape[:2]
    M = cv2.getRotationMatrix2D((width / 2, height / 2), angle_deg, 1)
    img = cv2.warpAffine(img, M, (width, height))
    return img

def get_angle(line):
    logger = logging.getLogger('repub.deskew')
    [vx,vy,x,y] = cv2.fitLine(np.array(line), cv2.DIST_L2,0,0.01,0.01)
    x_axis      = np.array([1, 0])    # unit vector in the same direction as the x axis
    your_line   = np.array([vx, vy])  # unit vector in the same direction as your line
    dot_product = np.dot(x_axis, your_line)
    angle_2_x   = np.arccos(dot_product)    
    ang         = angle_2_x[0]
    logger.warning('Unit vector: %.2f %.2f %.2f', vx, vy, ang)    
    if vy < 0:
        ang = -1 * ang
    return math.degrees(ang)

def get_lines_angle(lines):
    logger = logging.getLogger('repub.deskew')
    angles = []

    for line in lines:
        deg = get_angle(line)
        angles.append(deg)
    logger.warning('Angles: %s', angles)
    degrees = sum(angles)/len(angles)
    return degrees

def get_vlines_angle(vlines):
    angles = []
    for vline in vlines:
        angles.append(radian)
    radian = sum(angles)/len(angles)
    return math.degrees(radian)

