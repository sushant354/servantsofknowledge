import cv2

def threshold_gray(img, mingray, maxgray):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ret, thresh = cv2.threshold(gray, mingray, maxgray, cv2.THRESH_BINARY)
    return thresh

def find_contour(img):
    #edges = cv2.Canny(thresh, 50, 150, 3)
    thresh = threshold_gray(img, 125, 255)
    contours, hierarchy = cv2.findContours(thresh, cv2.RETR_EXTERNAL, \
                                           cv2.CHAIN_APPROX_SIMPLE )
    contours = sorted(contours, key=cv2.contourArea, reverse=True)
    return contours


