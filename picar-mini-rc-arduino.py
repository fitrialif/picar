#!/usr/bin/python
import os
import time
import atexit
import serial
import cv2
import math
import numpy as np
import sys
from threading import Thread,Lock

use_dnn = True
use_rec = True

if use_dnn:
    print ("Load TF")
    import tensorflow as tf
    import model
    import params
    import local_common as cm
    import preprocess

    print ("Load Model")
    sess = tf.InteractiveSession()
    saver = tf.train.Saver()
    model_name = 'model.ckpt'
    model_path = cm.jn(params.save_dir, model_name)
    saver.restore(sess, model_path)
    print ("Done..")
    
from pololu_drv8835_rpi import motors, MAX_SPEED

def deg2rad(deg):
    return deg * math.pi / 180.0
def rad2deg(rad):
    return 180.0 * rad / math.pi

def stop():
    global cur_speed
    cur_speed = 0
    motors.motor2.setSpeed(int(cur_speed))
        
def ffw():
    global cur_speed
    # cur_speed = min(MAX_SPEED, cur_speed + MAX_SPEED/10)
    cur_speed = SET_SPEED    
    motors.motor2.setSpeed(int(cur_speed))

def rew():
    global cur_speed        
    # cur_speed = max(-MAX_SPEED, cur_speed - MAX_SPEED/10)
    cur_speed = SET_SPEED
    motors.motor2.setSpeed(-int(cur_speed))

def center():
    motors.motor1.setSpeed(0)
def left():
    motors.motor1.setSpeed(MAX_SPEED)
def right():
    motors.motor1.setSpeed(-MAX_SPEED)

def turn_off(cap, vid, key, stat):
    stop()
    center()
    
    print "Closing the files..."
    cap.shutdown()
    key.close()
    vid.release()

    print "Control loop statistics"
    print "======================="
    print "min/avg/99pct/max (sec): {:.3f} {:.3f} {:.3f} {:.3f}".format(
        np.min(stat), np.mean(stat), np.percentile(stat, 99), np.max(stat))
    dmiss_cnt = sum(i > period for i in stat)
    print "deadline miss(es): {}/{} {:.1f}%".format(dmiss_cnt, len(stat),
                                                    float(dmiss_cnt)/len(stat)*100)
                                        
def g_tick():
    t = time.time()
    count = 0
    while True:
        count += 1
        yield max(t + count*period - time.time(),0)

class Camera:
    def __init__(self, res=(320, 240), fps=30):
        print "Initilize camera."
        self.cap = cv2.VideoCapture(0)
        self.cap.set(3, res[0]) # width
        self.cap.set(4, res[1]) # height
        self.cap.set(5, fps)
        self.frame = None
        self.enabled = True
        
    def update(self):
        while self.enabled:
            ret, self.frame = self.cap.read() # blocking read.

    def read_frame(self):
        return self.frame

    def shutdown(self):
        print ("Close the camera.")
        self.enabled = False;        
        self.cap.release()

cfg_width = 320
cfg_height = 240
cfg_fps = 30

cam = Camera((cfg_width, cfg_height), cfg_fps)
cam_thr = Thread(target=cam.update, args=())
cam_thr.start()
time.sleep(2)

frame_id = 0
angle = 0.0
btn   = 107
period = 0.05 # sec (=50ms)

motors.setSpeeds(0, 0)
# ser = serial.Serial('/dev/ttyACM0', 115200, timeout=1)
ser = serial.Serial('/dev/ttyUSB0', 115200, timeout=1)
# fourcc = cv2.VideoWriter_fourcc(*'XVID')
fourcc = cv2.cv.CV_FOURCC(*'XVID')
vidfile = cv2.VideoWriter('out-video.avi', fourcc, 
	                  cfg_fps, (cfg_width,cfg_height))
keyfile = open('out-key.csv', 'w+')
keyfile_btn = open('out-key-btn.csv', 'w+')
keyfile.write("ts_micro,frame,wheel\n")
keyfile_btn.write("ts_micro,frame,btn,speed\n")

SET_SPEED = MAX_SPEED * 7 / 10 
cur_speed = SET_SPEED
print "MAX speed:", MAX_SPEED
print "cur speed:", cur_speed

dstat = []
atexit.register(turn_off, cap=cam, vid=vidfile, key=keyfile, stat=dstat)

null_frame = np.zeros((cfg_width,cfg_height,3), np.uint8)
cv2.imshow('frame', null_frame)

if len(sys.argv) == 2:
    SET_SPEED = MAX_SPEED * int(sys.argv[1]) / 10 
    print "Set new speed: ", SET_SPEED

g = g_tick()

print ("Enter the main loop.")
m_ctrl = -99
view_video = False
rec_start_time = 0
ts = init_ts = time.time()
while True:
    time.sleep(next(g))    
    ts = time.time()

    # 0. read a frame
    frame = cam.read_frame()
        
    if view_video == True:
        cv2.imshow('frame', frame)

    # 1. machine input
    if use_dnn == True:
        print ("Perform an inference operation.")
        img = preprocess.preprocess(frame)
        angle_dnn = model.y.eval(feed_dict={model.x: [img], model.keep_prob: 1.0})[0][0]
        m_ctrl = get_control(rad2deg(angle_dnn)) 

    # 2. get RC input
    h_ctrl = -99    
    ser.write("getrc\n")
    line = ser.readline().rstrip("\n\r")
    rc_inputs = line.split()
    # print rc_inputs
    if len(rc_inputs) != 4:
        continue
    
    if int(rc_inputs[2]) == 0:
        # print "left"
        angle = deg2rad(-30)
        btn = h_ctrl = -1
    elif int(rc_inputs[3]) == 0:
        # print "right"
        angle = deg2rad(30)
        btn = h_ctrl = 1        
    elif int(rc_inputs[2]) > 0 and int(rc_inputs[3]) > 0:
        # print "center"
        angle = deg2rad(0)
        btn = h_ctrl = 0
    else:
        print ("INVALID steering input: {} {}".format(rc_inputs[2], rc_inputs[3]))

    if int(rc_inputs[0]) == 0:
        rec_start_time = ts 
        ffw()
        # print "accel"
    elif int(rc_inputs[1]) == 0:
        rew()
        # print "reverse"
    else:
        rec_start_time = 0         
        stop()
        # print "stop"                

    # 2. get keyboard input
    ch = cv2.waitKey(1) & 0xFF
    if ch == ord('q'):
        break
        
    # 3. decision
    if use_dnn == True and h_ctrl == 0:
        ctrl = m_ctrl
        ctrl_by = "M:"
    else:
        # if there's human input, use it.
        ctrl = h_ctrl
        ctrl_by = "H:"
        
    # 4. control
    if ctrl == 0:
        center()
    elif ctrl == 1:
        right()
    elif ctrl == -1:
        left()

    # statistics update
    dur = time.time() - ts
    if dur > period:
        print "{:.3f} deadline miss. dur={:.03f}".format(ts-init_ts, dur)
    if ts - init_ts > 1.0:
        # ignore first 1 sec.
        dstat.append(dur)
        
    if use_rec == True and rec_start_time > 0:
        # increase frame_id
        frame_id += 1
        
        # write input (angle)
        str = "{},{},{}\n".format(int(ts*1000), frame_id, angle)
        keyfile.write(str)
        
        # write input (button: left, center, stop, speed)
        str = "{},{},{},{}\n".format(int(ts*1000), frame_id, btn, cur_speed)
        keyfile_btn.write(str)
        
        # write video stream
        vidfile.write(frame)

        print ("%s %d %.3f %d %.3f %d %d(ms)" %
               (ctrl_by, ctrl, ts, frame_id, angle, btn, int(dur)*1000))
        
        if frame_id >= 400:
            print "recorded 400 frames"
            break

                                                 



stop()
cam.shutdown()
keyfile.close()
keyfile_btn.close()
vidfile.release()
    
