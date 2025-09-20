import cv2
import math
import numpy as np
import logging

from .utils import  find_contour, get_hvlines

def deskew(img, xmax, ymax, maxcontours, rotate_type, logger=None):
    if logger is None:
        logger = logging.getLogger('repub.deskew')
    contours = find_contour(img)

    contours = contours[:maxcontours]

    hlines, vlines = get_hvlines(contours, xmax, ymax, img.shape, logger)

    hangle = get_hlines_angle(hlines, logger)
    logger.warning('Hangle: %s', hangle)
    vangle = get_vlines_angle(vlines, logger)
    logger.warning('Vangle: %s', vangle)

    angle = merge_angles(hangle, vangle, rotate_type) 
    if angle and abs(angle) > 0:
        img = rotate(img, angle)
    return img, angle

def merge_angles(hangle, vangle, rotate_type):
    angle = None
    if rotate_type == 'horizontal' and hangle and abs(hangle) > 0:
        angle = hangle
    elif rotate_type == 'vertical' and vangle and abs(vangle) > 0:
        angle = vangle
    elif rotate_type == 'overall':
        avg = 0
        num = 0
        if vangle and abs(vangle) > 0:
            avg = vangle
            num += 1
        if hangle and abs(hangle) > 0:
            avg += hangle
            num += 1
        if num > 0:
            angle = avg/num

    return angle

def rotate(img, angle_deg):
    height, width = img.shape[:2]
    M = cv2.getRotationMatrix2D((width / 2, height / 2), angle_deg, 1)
    img = cv2.warpAffine(img, M, (width, height))
    return img

def get_angle(line, logger=None):
    if logger is None:
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

def get_vlines_angle(lines, logger=None):
    if logger is None:
        logger = logging.getLogger('repub.deskew')
    angles = []

    for line in lines:
        deg = get_angle(line, logger)
        if deg < 0:
            deg = 90+deg
        else:
            deg = -90+deg
        angles.append(deg)
    logger.warning('Angles: %s', angles)
    degrees = sum(angles)/len(angles)
    return degrees

def get_hlines_angle(lines, logger=None):
    if logger is None:
        logger = logging.getLogger('repub.deskew')
    angles = []

    for line in lines:
        deg = get_angle(line, logger)
        angles.append(deg)
    logger.warning('Angles: %s', angles)
    degrees = sum(angles)/len(angles)
    return degrees

