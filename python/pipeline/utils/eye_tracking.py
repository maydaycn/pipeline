import warnings
from collections import defaultdict

import h5py
import numpy as np
from scipy.interpolate import InterpolatedUnivariateSpline as iu_spline
from pipeline import PipelineException
import matplotlib
import pandas as pd

matplotlib.use('agg')
import matplotlib.pyplot as plt
import numpy as np

try:
    import cv2
except ImportError:
    print("Could not find cv2. You won't be able to use the pupil tracker.")

ANALOG_PACKET_LEN = 2000

def read_video_hdf5(hdf_path):
    """
    Reads hdf5 file for eye tracking

    :param hdf_path: path of the file. Needs a %d where multiple files differ.
    :return: dictionary with the data
    """
    data = {}
    with h5py.File(hdf_path, 'r+', driver='family', memb_size=0) as fid:

        data['ball'] = np.asarray(fid['ball']).T
        wf = np.asarray(np.asarray(fid['waveform'])).T
        data['cam1ts'] = np.asarray(fid['behaviorvideotimestamp']).squeeze()
        data['cam2ts'] = np.asarray(fid['eyetrackingvideotimestamp']).squeeze()


        data['syncPd'] = wf[:, 2] # flip photo diode
        data['scanImage'] = wf[:, 9]
        data['ts'] = wf[:, 10]
        data['analogPacketLen'] = ANALOG_PACKET_LEN

    return data


def ts2sec(ts, packet_length=0, samplingrate=1e7):
    """
    Convert 10MHz timestamps from Saumil's patching program (ts) to seconds (s)

    :param ts: timestamps
    :param packet_length: length of timestamped packets
    :returns:
        timestamps converted to seconds
        system time (in seconds) of t=0
        bad camera indices from 2^31:2^32 in camera timestamps prior to 4/10/13
    """
    ts = ts.astype(float)

    # find bad indices in camera timestamps and replace with linear est
    bad_idx = ts == 2 ** 31 - 1
    if bad_idx.sum() > 10:
        raise PipelineException('Bad camera ts...')
        x = np.where(~bad_idx)[0]
        x_bad = np.where(bad_idx)[0]
        f = iu_spline(x, ts[~bad_idx], k=1)
        ts[bad_idx] = f(x_bad)

    # remove wraparound
    wrap_idx = np.where(np.diff(ts) < 0)[0]
    while not len(wrap_idx) == 0:
        ts[wrap_idx[0] + 1:] += 2 ** 32
        wrap_idx = np.where(np.diff(ts) < 0)[0]

    s = ts / samplingrate

    # Remove offset, and if not monotonically increasing (i.e. for packeted ts), interpolate
    if np.any(np.diff(s) <= 0):
        # Check to make sure it's packets
        diffs = np.where(np.diff(s) > 0)[0]
        assert packet_length == diffs[0] + 1

        # Interpolate
        not_zero = np.hstack((0, diffs + 1))
        f = iu_spline(not_zero, s[not_zero], k=1)
        s = f(np.arange(len(s)))

    return s, bad_idx

class CVROIGrabber:
    start = None
    end = None
    roi = None

    def __init__(self, img):
        self.img = img
        self.exit = False


    def grab(self):
        img = np.asarray(self.img/self.img.max(), dtype=float)
        cv2.namedWindow('real image')
        cv2.setMouseCallback('real image', self, 0)

        while not self.exit:
            cv2.imshow('real image', img)
            if (cv2.waitKey(0) & 0xFF) == ord('q'):
                cv2.waitKey(1)
                cv2.destroyAllWindows()
                break
        cv2.waitKey(2)

    def __call__(self, event, x, y, flags, params):
        img = self.img
        if event == cv2.EVENT_LBUTTONDOWN:
            print('Start Mouse Position: '+str(x)+', '+str(y))
            self.start = np.asarray([x,y])

        elif event == cv2.EVENT_LBUTTONUP:
            self.end = np.asarray([x, y])
            x = np.vstack((self.start, self.end))
            tmp = np.hstack((x.min(axis=0), x.max(axis=0)))
            roi = np.asarray([[tmp[1], tmp[3]], [tmp[0], tmp[2]]], dtype=int) + 1
            print(roi)
            crop = img[roi[0,0]:roi[0,1],roi[1,0]:roi[1,1]]
            crop = np.asarray(crop/crop.max(), dtype=float)
            self.roi = roi
            cv2.imshow('crop',crop)
            if (cv2.waitKey(0) & 0xFF) == ord('q'):
                cv2.destroyAllWindows()
                self.exit = True


class ROIGrabber:
    """
    Interactive matplotlib figure to grab an ROI from an image.

    Usage:

    rg = ROIGrabber(img)
    # select roi
    print(rg.roi) # get ROI
    """

    def __init__(self, img):
        plt.switch_backend('GTK3Agg')
        self.img = img
        self.start = None
        self.current = None
        self.end = None
        self.pressed = False
        self.fig, self.ax = plt.subplots(facecolor='w')

        self.fig.canvas.mpl_connect('button_press_event', self.on_press)
        self.fig.canvas.mpl_connect('button_release_event', self.on_release)
        self.fig.canvas.mpl_connect('motion_notify_event', self.on_move)
        self.replot()
        plt.show(block=True)

    def draw_rect(self, fr, to, color='dodgerblue'):
        x = np.vstack((fr, to))
        fr = x.min(axis=0)
        to = x.max(axis=0)
        self.ax.plot(fr[0] * np.ones(2), [fr[1], to[1]], color=color, lw=2)
        self.ax.plot(to[0] * np.ones(2), [fr[1], to[1]], color=color, lw=2)
        self.ax.plot([fr[0], to[0]], fr[1] * np.ones(2), color=color, lw=2)
        self.ax.plot([fr[0], to[0]], to[1] * np.ones(2), color=color, lw=2)
        self.ax.plot(fr[0], fr[1], 'ok', mfc='gold')
        self.ax.plot(to[0], to[1], 'ok', mfc='deeppink')

    def replot(self):
        self.ax.clear()
        self.ax.imshow(self.img, cmap=plt.cm.gray)

        if self.pressed:
            self.draw_rect(self.start, self.current, color='lime')
        elif self.start is not None and self.end is not None:
            self.draw_rect(self.start, self.current)
        self.ax.axis('tight')
        self.ax.set_aspect(1)
        self.ax.set_title('Close window when done', fontsize=16, fontweight='bold')
        plt.draw()

    @property
    def roi(self):
        x = np.vstack((self.start, self.end))
        tmp = np.hstack((x.min(axis=0), x.max(axis=0)))
        return np.asarray([[tmp[1], tmp[3]], [tmp[0], tmp[2]]], dtype=int) + 1

    def on_press(self, event):
        if event.xdata is not None and event.ydata is not None:
            self.pressed = True
            self.start = np.asarray([event.xdata, event.ydata])

    def on_release(self, event):
        if event.xdata is not None and event.ydata is not None:
            self.end = np.asarray([event.xdata, event.ydata])
        else:
            self.end = self.current
        self.pressed = False
        self.replot()

    def on_move(self, event):
        if event.xdata is not None and event.ydata is not None:
            self.current = np.asarray([event.xdata, event.ydata])
            if self.pressed:
                self.replot()


class PupilTracker:
    def __init__(self, param):
        self._params = param

    @staticmethod
    def goodness_of_fit(contour, ellipse):
        center, size, angle = ellipse
        angle *= np.pi / 180
        err = 0
        for coord in contour.squeeze().astype(np.float):
            posx = (coord[0] - center[0]) * np.cos(-angle) - (coord[1] - center[1]) * np.sin(-angle)
            posy = (coord[0] - center[0]) * np.sin(-angle) + (coord[1] - center[1]) * np.cos(-angle)
            err += ((posx / size[0]) ** 2 + (posy / size[1]) ** 2 - 0.25) ** 2

        return np.sqrt(err / len(contour))

    @staticmethod
    def restrict_to_long_axis(contour, ellipse, corridor):
        center, size, angle = ellipse
        angle *= np.pi / 180
        R = np.asarray([[np.cos(-angle), - np.sin(-angle)],[np.sin(-angle), np.cos(-angle)]])
        contour = np.dot(contour.squeeze() - center, R.T)
        contour = contour[np.abs(contour[:,0]) < corridor*ellipse[1][1]/2]
        return (np.dot(contour, R)+center).astype(np.int32)


    def get_pupil_from_contours(self, contours, small_gray, old_center, old_r, display=False, show_matching=5):
        ratio_thres = self._params['ratio_threshold']
        area_threshold = self._params['relative_area_threshold']
        error_threshold = self._params['error_threshold']
        min_contour = self._params['min_contour_len']
        margin = self._params['margin']
        speed_thres = self._params['speed_threshold']
        dr_thres = self._params['dr_threshold']
        err = np.inf
        best_ellipse = None
        best_contour = None
        results = defaultdict(list)

        for j, cnt in enumerate(contours):
            if len(contours[j]) < min_contour:  # otherwise fitEllipse won't work
                continue

            ellipse = cv2.fitEllipse(contours[j])
            ((x, y), axes, angle) = ellipse
            if min(axes) == 0:  # otherwise ratio won't work
                continue

            ratio = max(axes) / min(axes)
            area = np.prod(ellipse[1]) / np.prod(small_gray.shape)
            curr_err = self.goodness_of_fit(cnt, ellipse)
            results['ratio'].append(ratio)
            results['area'].append(area)
            results['rmse'].append(curr_err)
            results['x coord'].append(x / small_gray.shape[1])
            results['y coord'].append(y / small_gray.shape[0])

            center = np.array([x / small_gray.shape[1], y / small_gray.shape[0]])
            r = max(axes)

            dr = 0 if old_r is None else abs(r-old_r)/old_r
            dx = 0 if old_center is None else np.sqrt(np.sum((center - old_center) ** 2))

            results['dx'].append(dx)
            results['dr/r'].append(dr)
            matching_conditions = 1 * (ratio <= ratio_thres) + 1 * (area >= area_threshold) \
                                  + 1 * (curr_err < error_threshold) \
                                  + 1 * (margin < center[0] < 1 - margin) \
                                  + 1 * (margin < center[1] < 1 - margin) \
                                  + 1 * (dx < speed_thres) \
                                  + 1 * (dr < dr_thres)

            results['conditions'] = matching_conditions

            if curr_err < err and matching_conditions == 7:
                best_ellipse = ellipse
                best_contour = cnt
                err = curr_err
                if display:
                    cv2.ellipse(small_gray, ellipse, (0, 0, 255), 2)
            elif matching_conditions >= show_matching and display:
                cv2.ellipse(small_gray, ellipse, (255, 0, 0), 2)
        if best_ellipse is None:
            df = pd.DataFrame(results)
            print('-', end="", flush=True)
            if np.any(df['conditions'] >= show_matching):
                print("\n",df[df['conditions'] >= show_matching], flush=True)

        return best_contour, best_ellipse

    def track(self, videofile, eye_roi, display=False):

        cw_low = self._params['perc_weight']
        p_high = self._params['perc_high']
        p_low = self._params['perc_low']
        contrast_low = self._params['contrast_threshold']

        print("Tracking videofile", videofile)

        cap = cv2.VideoCapture(videofile)
        traces = []

        no_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        fr_count = 0
        old_center = None
        old_r = None
        while cap.isOpened():
            if fr_count >= no_frames:
                print("Reached end of videofile ", videofile)
                break
            ret, frame = cap.read()
            fr_count += 1
            if not ret:
                traces.append(dict(frame_id=fr_count))
                continue
            if fr_count % 500 == 0:
                print("\tframe ({}/{})".format(fr_count, no_frames))

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            img_std = np.std(gray)

            if img_std < contrast_low:
                traces.append(dict(frame_id=fr_count,
                                  frame_intensity=img_std))
                if display:
                    cv2.imshow('frame', gray)
                    cv2.waitKey(1)
                print('_', end="",flush=True)
                continue

            small_gray = gray[slice(*eye_roi[0]), slice(*eye_roi[1])]
            blur = cv2.GaussianBlur(small_gray, (9, 9), 0)
            th = (1 - cw_low) * np.percentile(blur, p_high) + cw_low * np.percentile(blur, p_low)
            _, thres = cv2.threshold(blur, th, 255, cv2.THRESH_BINARY)

            if display:
                cv2.imshow('blur', blur)
                cv2.imshow('threshold', thres)

            _, contours, hierarchy1 = cv2.findContours(thres, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
            contour, ellipse = self.get_pupil_from_contours(contours, small_gray, old_center, old_r,  display=display)

            if contour is None:
                traces.append(dict(frame_id=fr_count,
                                  frame_intensity=img_std))
                old_center = None
                old_r = None
            else:
                eye_center = eye_roi[::-1, 0] + np.asarray(ellipse[0])
                old_center = np.asarray(ellipse[0])/np.asarray(small_gray.shape[::-1])
                old_r = max(ellipse[1])
                traces.append(dict(center=eye_center,
                                  major_r=np.max(ellipse[1]),
                                  rotated_rect=np.hstack(ellipse),
                                  contour=contour.astype(np.int16),
                                  frame_id=fr_count,
                                  frame_intensity=img_std
                                  ))
            if display:
                if contour is not None:
                    ellipse = list(ellipse)
                    ellipse[0] = tuple(eye_center)
                    ellipse = tuple(ellipse)
                    cv2.drawContours(gray, [contour], 0, (255, 0, 0), 1, offset=tuple(eye_roi[::-1, 0]))
                    cv2.ellipse(gray, ellipse, (0, 0, 255), 2)
                    epy, epx = np.round(eye_center).astype(int)
                    gray[epx - 2:epx + 2, epy - 2:epy + 2] = 0
                cv2.imshow('frame', gray)

            if (cv2.waitKey(1) & 0xFF == ord('q')):
                raise PipelineException('Tracking aborted')

        cap.release()
        cv2.destroyAllWindows()

        return traces
