import torch
import torch.nn as nn
import numpy as np
import cv2
import matplotlib
import matplotlib.pyplot as plt

def xyxy2xywh(x):
    # Convert bounding box format from [x1, y1, x2, y2] to [x, y, w, h]
    y = torch.zeros_like(x) if isinstance(x, torch.Tensor) else np.zeros_like(x)
    y[:, 0] = (x[:, 0] + x[:, 2]) / 2
    y[:, 1] = (x[:, 1] + x[:, 3]) / 2
    y[:, 2] = x[:, 2] - x[:, 0]
    y[:, 3] = x[:, 3] - x[:, 1]
    return y

def xywh2xyxy(x):
    # Convert bounding box format from [x, y, w, h] to [x1, y1, x2, y2]
    y = torch.zeros_like(x) if isinstance(x, torch.Tensor) else np.zeros_like(x)
    y[:, 0] = x[:, 0] - x[:, 2] / 2
    y[:, 1] = x[:, 1] - x[:, 3] / 2
    y[:, 2] = x[:, 0] + x[:, 2] / 2
    y[:, 3] = x[:, 1] + x[:, 3] / 2
    return y

def letterbox(img, height=416, color=(127.5, 127.5, 127.5)):
    # Resize a rectangular image to a padded square
    shape = img.shape[:2]  # shape = [height, width]
    ratio = float(height) / max(shape)  # ratio  = old / new
    new_shape = (round(shape[1] * ratio), round(shape[0] * ratio))
    dw = (height - new_shape[0]) / 2  # width padding
    dh = (height - new_shape[1]) / 2  # height padding
    top, bottom = round(dh - 0.1), round(dh + 0.1)
    left, right = round(dw - 0.1), round(dw + 0.1)
    img = cv2.resize(img, new_shape, interpolation=cv2.INTER_AREA)  # resized, no border
    img = cv2.copyMakeBorder(img, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color)  # padded square
    return img, ratio, dw, dh

def model_info(model):
    # Plots a line-by-line description of a PyTorch model
    n_p = sum(x.numel() for x in model.parameters())  # number parameters
    n_g = sum(x.numel() for x in model.parameters() if x.requires_grad)  # number gradients
    print('\n%5s %40s %9s %12s %20s %10s %10s' % ('layer', 'name', 'gradient', 'parameters', 'shape', 'mu', 'sigma'))
    for i, (name, p) in enumerate(model.named_parameters()):
        name = name.replace('module_list.', '')
        print('%5g %40s %9s %12g %20s %10.3g %10.3g' % (
            i, name, p.requires_grad, p.numel(), list(p.shape), p.mean(), p.std()))
    print('Model Summary: %g layers, %g parameters, %g gradients' % (i + 1, n_p, n_g))
    
def plot_images(imgs, targets, fname='images.jpg'):
    # Plots training images overlaid with targets
    imgs = imgs.cpu().numpy()
    targets = targets.cpu().numpy()

    fig = plt.figure(figsize=(10, 10))
    img_size = imgs.shape[3]
    bs = imgs.shape[0]  # batch size
    sp = np.ceil(bs ** 0.5)  # subplots

    for i in range(bs):
        boxes = xywh2xyxy(targets[targets[:, 0] == i, 2:6]).T * img_size
        plt.subplot(sp, sp, i + 1).imshow(imgs[i].transpose(1, 2, 0))
        plt.plot(boxes[[0, 2, 2, 0, 0]], boxes[[1, 1, 3, 3, 1]], '.-')
        plt.axis('off')
    fig.tight_layout()
    fig.savefig(fname, dpi=300)
    plt.close()
    
def build_targets(model, targets):
    # targets = [image, class, x, y, w, h]
    """
    tensor([[ 0.00000, 45.00000,  0.49307,  0.60885,  0.91218,  0.42523],
        [ 0.00000, 45.00000,  0.73586,  0.29364,  0.47691,  0.34016],
        [ 0.00000, 50.00000,  0.64325,  0.64040,  0.47253,  0.36455],
        [ 0.00000, 45.00000,  0.35859,  0.41617,  0.64955,  0.55797],
        [ 0.00000, 49.00000,  0.65001,  0.21179,  0.11278,  0.06922],
        [ 0.00000, 49.00000,  0.77024,  0.20984,  0.08678,  0.06942],
        [ 0.00000, 49.00000,  0.67084,  0.27915,  0.12559,  0.10488],
        [ 0.00000, 49.00000,  0.64599,  0.17371,  0.14157,  0.10572]])
    """
    if type(model) in (nn.parallel.DataParallel, nn.parallel.DistributedDataParallel):
        model = model.module

    nt = len(targets)
    txy, twh, tcls, indices = [], [], [], []
    for i in model.yolo_layers:
        layer = model.module_list[i][0]

        # iou of targets-anchors
        t, a = targets, []
        gwh = targets[:, 4:6] * layer.nG
        """
        tensor([[11.85836,  5.52798],
        [ 6.19979,  4.42211],
        [ 6.14289,  4.73918],
        [ 8.44410,  7.25367],
        [ 1.46616,  0.89980],
        [ 1.12818,  0.90246],
        [ 1.63269,  1.36346],
        [ 1.84043,  1.37430]])
        """
        if nt:
            iou = [wh_iou(x, gwh) for x in layer.anchor_vec] # shape:3*objects(8)
            iou, a = torch.stack(iou, 0).max(0)  # best iou and anchor, shape:1*objects(8)
            # iou tensor([0.52454, 0.60101, 0.63675, 0.57156, 0.20339, 0.15697, 0.34320, 0.38994], device='cuda:0')
            # a tensor([2, 1, 1, 2, 0, 0, 0, 0], device='cuda:0')

            # reject below threshold ious (OPTIONAL, increases P, lowers R)
            reject = True # 滤除iou<=0.10的target,防止偏移过大导致loss不稳定，只计算iou大于阈值的loss
            if reject:
                j = iou > 0.10
                t, a, gwh = targets[j], a[j], gwh[j]

        # Indices
        b, c = t[:, :2].long().t()  # target image, class, b tensor([0, 0, 0, 0, 0, 0, 0, 0]), c tensor([45, 45, 50, 45, 49, 49, 49, 49])
        gxy = t[:, 2:4] * layer.nG # torch.Size([8, 2])
        """
        tensor([[ 6.40985,  7.91507],
        [ 9.56623,  3.81726],
        [ 8.36229,  8.32523],
        [ 4.66172,  5.41020],
        [ 8.45008,  2.75321],
        [10.01309,  2.72789],
        [ 8.72088,  3.62892],
        [ 8.39792,  2.25823]])
        """
        gi, gj = gxy.long().t()  # grid_i, grid_j, 取整，得到坐标所在的网格， 6.8->6, 9.1->9, tensor([ 6,  9,  8,  4,  8, 10,  8,  8]), tensor([7, 3, 8, 5, 2, 2, 3, 2])
        indices.append((b, a, gj, gi)) # index, anchor, grid j, grid i

        # XY coordinates
        txy.append(gxy - gxy.floor())
        """ 
        <class 'list'>: 
        [tensor([[0.40985, 0.91507],
        [0.56623, 0.81726],
        [0.36229, 0.32523],
        [0.66172, 0.41020],
        [0.45008, 0.75321],
        [0.01309, 0.72789],
        [0.72088, 0.62892],
        [0.39792, 0.25823]])]"""

        # Width and height
        twh.append(torch.log(gwh / layer.anchor_vec[a]))  # wh yolo method
        """
        <class 'list'>: [tensor([[ 0.09813, -0.58963],
        [ 0.38498, -0.17755],
        [ 0.37576, -0.10830],
        [-0.24144, -0.31795],
        [-0.54607, -1.04657],
        [-0.80810, -1.04361],
        [-0.43849, -0.63096],
        [-0.31872, -0.62304]])]
        """
        # twh.append((gwh / layer.anchor_vec[a]) ** (1 / 3) / 2)  # wh power method

        # Class
        tcls.append(c) # <class 'list'>: [tensor([45, 45, 50, 45, 49, 49, 49, 49])]
        if c.shape[0]:
            assert c.max() <= layer.nC, 'Target classes exceed model classes'

    return txy, twh, tcls, indices
    # txy: list of len 2, torch.Size([8, 2]), torch.Size([4, 2]) 维度不一样是reject掉了iou<=0.10
    # twh: same as txy
    # tcls: list of len 2, tensor([45, 45, 50, 45, 49, 49, 49, 49]), tensor([49, 49, 49, 49])
    # indices: list of len 2, 4*8, 4*4


def compute_loss(p, targets):  # predictions, targets
    # p: list of len 2, torch.Size([1, 3, 13, 13, 85]), torch.Size([1, 3, 26, 26, 85]), from yolo-tiny outputs
    # targets： txy, twh, tcls, indices, from building_traget()
    FT = torch.cuda.FloatTensor if p[0].is_cuda else torch.Tensor
    lxy, lwh, lcls, lconf = FT([0]), FT([0]), FT([0]), FT([0])
    txy, twh, tcls, indices = targets
    MSE = nn.MSELoss()
    CE = nn.CrossEntropyLoss() # yolov3 binary CE
    BCE = nn.BCEWithLogitsLoss()

    # Compute losses
    # gp = [x.numel() for x in tconf]  # grid points
    for i, pi0 in enumerate(p):  # layer i predictions, i
        b, a, gj, gi = indices[i]  # image, anchor, gridx, gridy
        # b tensor([0, 0, 0, 0, 0, 0, 0, 0])
        # a tensor([2, 1, 1, 2, 0, 0, 0, 0])
        # gj tensor([9, 5, 10, 6, 3, 3,4, 3])
        # gi tensor([6, 3, 4, 8, 4, 2, 3, 4])
        tconf = torch.zeros_like(pi0[..., 0])  # conf torch.Size([1, 3, 13, 13]),
        nt = len(b)  # number of targets

        # Compute losses
        k = 1  # nt / bs
        if nt:
            pi = pi0[b, a, gj, gi]  # predictions closest to anchors, torch.Size([8, 85]), 
            tconf[b, a, gj, gi] = 1  # conf

            lxy += (k * 8) * MSE(torch.sigmoid(pi[..., 0:2]), txy[i])  # xy loss
            lwh += (k * 1) * MSE(pi[..., 2:4], twh[i])  # wh yolo loss
            # lwh += (k * 1) * MSE(torch.sigmoid(pi[..., 2:4]), twh[i])  # wh power loss
            lcls += (k * 1) * CE(pi[..., 5:], tcls[i])  # class_conf loss

        # pos_weight = FT([gp[i] / min(gp) * 4.])
        # BCE = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
        lconf += (k * 64) * BCE(pi0[..., 4], tconf)  # obj_conf loss
    loss = lxy + lwh + lconf + lcls

    return loss, torch.cat((lxy, lwh, lconf, lcls, loss)).detach()

def load_classes(path):
    # Loads class labels at 'path'
    with open(path, 'r') as fp:
        names = fp.read().split('\n')
    return list(filter(None, names))  # filter removes empty strings (such as last line)

def non_max_suppression(prediction, conf_thres=0.5, nms_thres=0.5):
    """
    Removes detections with lower object confidence score than 'conf_thres'
    Non-Maximum Suppression to further filter detections.
    Returns detections with shape:
        (x1, y1, x2, y2, object_conf, class_conf, class)
    """

    min_wh = 2  # (pixels) minimum box width and height

    output = [None] * len(prediction)
    for image_i, pred in enumerate(prediction):
        # Experiment: Prior class size rejection
        # x, y, w, h = pred[:, 0], pred[:, 1], pred[:, 2], pred[:, 3]
        # a = w * h  # area
        # ar = w / (h + 1e-16)  # aspect ratio
        # n = len(w)
        # log_w, log_h, log_a, log_ar = torch.log(w), torch.log(h), torch.log(a), torch.log(ar)
        # shape_likelihood = np.zeros((n, 60), dtype=np.float32)
        # x = np.concatenate((log_w.reshape(-1, 1), log_h.reshape(-1, 1)), 1)
        # from scipy.stats import multivariate_normal
        # for c in range(60):
        # shape_likelihood[:, c] =
        #   multivariate_normal.pdf(x, mean=mat['class_mu'][c, :2], cov=mat['class_cov'][c, :2, :2])

        # Multiply conf by class conf to get combined confidence
        class_conf, class_pred = pred[:, 5:].max(1)
        pred[:, 4] *= class_conf

        # Select only suitable predictions
        i = (pred[:, 4] > conf_thres) & (pred[:, 2:4] > min_wh).all(1) & (torch.isnan(pred).any(1) == 0)
        pred = pred[i]

        # If none are remaining => process next image
        if len(pred) == 0:
            continue

        # Select predicted classes
        class_conf = class_conf[i]
        class_pred = class_pred[i].unsqueeze(1).float()

        # Box (center x, center y, width, height) to (x1, y1, x2, y2)
        pred[:, :4] = xywh2xyxy(pred[:, :4])
        # pred[:, 4] *= class_conf  # improves mAP from 0.549 to 0.551

        # Detections ordered as (x1y1x2y2, obj_conf, class_conf, class_pred)
        pred = torch.cat((pred[:, :5], class_conf.unsqueeze(1), class_pred), 1)

        # Get detections sorted by decreasing confidence scores
        pred = pred[(-pred[:, 4]).argsort()]

        det_max = []
        nms_style = 'MERGE'  # 'OR' (default), 'AND', 'MERGE' (experimental)
        for c in pred[:, -1].unique():
            dc = pred[pred[:, -1] == c]  # select class c
            dc = dc[:min(len(dc), 100)]  # limit to first 100 boxes: https://github.com/ultralytics/yolov3/issues/117

            # No NMS required if only 1 prediction
            if len(dc) == 1:
                det_max.append(dc)
                continue

            # Non-maximum suppression
            if nms_style == 'OR':  # default
                # METHOD1
                # ind = list(range(len(dc)))
                # while len(ind):
                # j = ind[0]
                # det_max.append(dc[j:j + 1])  # save highest conf detection
                # reject = (bbox_iou(dc[j], dc[ind]) > nms_thres).nonzero()
                # [ind.pop(i) for i in reversed(reject)]

                # METHOD2
                while dc.shape[0]:
                    det_max.append(dc[:1])  # save highest conf detection
                    if len(dc) == 1:  # Stop if we're at the last detection
                        break
                    iou = bbox_iou(dc[0], dc[1:])  # iou with other boxes
                    dc = dc[1:][iou < nms_thres]  # remove ious > threshold

            elif nms_style == 'AND':  # requires overlap, single boxes erased
                while len(dc) > 1:
                    iou = bbox_iou(dc[0], dc[1:])  # iou with other boxes
                    if iou.max() > 0.5:
                        det_max.append(dc[:1])
                    dc = dc[1:][iou < nms_thres]  # remove ious > threshold

            elif nms_style == 'MERGE':  # weighted mixture box
                while len(dc):
                    if len(dc) == 1:
                        det_max.append(dc)
                        break
                    i = bbox_iou(dc[0], dc) > nms_thres  # iou with other boxes
                    weights = dc[i, 4:5]
                    dc[0, :4] = (weights * dc[i, :4]).sum(0) / weights.sum()
                    det_max.append(dc[:1])
                    dc = dc[i == 0]

        if len(det_max):
            det_max = torch.cat(det_max)  # concatenate
            output[image_i] = det_max[(-det_max[:, 4]).argsort()]  # sort

    return output


def ap_per_class(tp, conf, pred_cls, target_cls):
    """ Compute the average precision, given the recall and precision curves.
    Source: https://github.com/rafaelpadilla/Object-Detection-Metrics.
    # Arguments
        tp:    True positives (list).
        conf:  Objectness value from 0-1 (list).
        pred_cls: Predicted object classes (list).
        target_cls: True object classes (list).
    # Returns
        The average precision as computed in py-faster-rcnn.
    """

    # Sort by objectness
    i = np.argsort(-conf)
    tp, conf, pred_cls = tp[i], conf[i], pred_cls[i]

    # Find unique classes
    unique_classes = np.unique(target_cls)

    # Create Precision-Recall curve and compute AP for each class
    ap, p, r = [], [], []
    for c in unique_classes:
        i = pred_cls == c
        n_gt = (target_cls == c).sum()  # Number of ground truth objects
        n_p = i.sum()  # Number of predicted objects

        if n_p == 0 and n_gt == 0:
            continue
        elif n_p == 0 or n_gt == 0:
            ap.append(0)
            r.append(0)
            p.append(0)
        else:
            # Accumulate FPs and TPs
            fpc = (1 - tp[i]).cumsum()
            tpc = (tp[i]).cumsum()

            # Recall
            recall_curve = tpc / (n_gt + 1e-16)
            r.append(recall_curve[-1])

            # Precision
            precision_curve = tpc / (tpc + fpc)
            p.append(precision_curve[-1])

            # AP from recall-precision curve
            ap.append(compute_ap(recall_curve, precision_curve))

            # Plot
            # plt.plot(recall_curve, precision_curve)

    # Compute F1 score (harmonic mean of precision and recall)
    p, r, ap = np.array(p), np.array(r), np.array(ap)
    f1 = 2 * p * r / (p + r + 1e-16)

    return p, r, ap, f1, unique_classes.astype('int32')

def wh_iou(box1, box2):
    # Returns the IoU of wh1 to wh2. wh1 is 2, wh2 is nx2
    box2 = box2.t()

    # w, h = box1
    w1, h1 = box1[0], box1[1]
    w2, h2 = box2[0], box2[1]

    # Intersection area
    inter_area = torch.min(w1, w2) * torch.min(h1, h2)

    # Union Area
    union_area = (w1 * h1 + 1e-16) + w2 * h2 - inter_area

    return inter_area / union_area  # iou

def bbox_iou(box1, box2, x1y1x2y2=True):
    # Returns the IoU of box1 to box2. box1 is 4, box2 is nx4
    box2 = box2.t()

    # Get the coordinates of bounding boxes
    if x1y1x2y2:
        # x1, y1, x2, y2 = box1
        b1_x1, b1_y1, b1_x2, b1_y2 = box1[0], box1[1], box1[2], box1[3]
        b2_x1, b2_y1, b2_x2, b2_y2 = box2[0], box2[1], box2[2], box2[3]
    else:
        # x, y, w, h = box1
        b1_x1, b1_x2 = box1[0] - box1[2] / 2, box1[0] + box1[2] / 2
        b1_y1, b1_y2 = box1[1] - box1[3] / 2, box1[1] + box1[3] / 2
        b2_x1, b2_x2 = box2[0] - box2[2] / 2, box2[0] + box2[2] / 2
        b2_y1, b2_y2 = box2[1] - box2[3] / 2, box2[1] + box2[3] / 2

    # Intersection area
    inter_area = (torch.min(b1_x2, b2_x2) - torch.max(b1_x1, b2_x1)).clamp(0) * \
                 (torch.min(b1_y2, b2_y2) - torch.max(b1_y1, b2_y1)).clamp(0)

    # Union Area
    union_area = ((b1_x2 - b1_x1) * (b1_y2 - b1_y1) + 1e-16) + \
                 (b2_x2 - b2_x1) * (b2_y2 - b2_y1) - inter_area

    return inter_area / union_area  # iou

def plot_results(start=0, stop=0):  # from utils.utils import *; plot_results()
    # Plot training results files 'results*.txt'
    # import os; os.system('wget https://storage.googleapis.com/ultralytics/yolov3/results_v3.txt')

    fig = plt.figure(figsize=(14, 7))
    s = ['X + Y', 'Width + Height', 'Confidence', 'Classification', 'Train Loss', 'Precision', 'Recall', 'mAP', 'F1',
         'Test Loss']
    for f in sorted(glob.glob('results*.txt')):
        results = np.loadtxt(f, usecols=[2, 3, 4, 5, 6, 9, 10, 11, 12, 13]).T
        n = results.shape[1]  # number of rows
        x = range(start, min(stop, n) if stop else n)
        for i in range(10):
            plt.subplot(2, 5, i + 1)
            plt.plot(x, results[i, x], marker='.', label=f.replace('.txt', ''))
            plt.title(s[i])
            if i == 0:
                plt.legend()
    fig.tight_layout()
    fig.savefig('results.png', dpi=300)
    
def compute_ap(recall, precision):
    """ Compute the average precision, given the recall and precision curves.
    Source: https://github.com/rbgirshick/py-faster-rcnn.
    # Arguments
        recall:    The recall curve (list).
        precision: The precision curve (list).
    # Returns
        The average precision as computed in py-faster-rcnn.
    """
    # correct AP calculation
    # first append sentinel values at the end

    mrec = np.concatenate(([0.], recall, [1.]))
    mpre = np.concatenate(([0.], precision, [0.]))

    # compute the precision envelope
    for i in range(mpre.size - 1, 0, -1):
        mpre[i - 1] = np.maximum(mpre[i - 1], mpre[i])

    # to calculate area under PR curve, look for points
    # where X axis (recall) changes value
    i = np.where(mrec[1:] != mrec[:-1])[0]

    # and sum (\Delta recall) * prec
    ap = np.sum((mrec[i + 1] - mrec[i]) * mpre[i + 1])
    return ap

def scale_coords(img_size, coords, img0_shape):
    # Rescale x1, y1, x2, y2 from 416 to image size
    gain = float(img_size) / max(img0_shape)  # gain  = old / new
    pad_x = (img_size - img0_shape[1] * gain) / 2  # width padding
    pad_y = (img_size - img0_shape[0] * gain) / 2  # height padding
    coords[:, [0, 2]] -= pad_x
    coords[:, [1, 3]] -= pad_y
    coords[:, :4] /= gain
    coords[:, :4] = torch.clamp(coords[:, :4], min=0)
    return coords

def plot_one_box(x, img, color=None, label=None, line_thickness=None):
    # Plots one bounding box on image img
    tl = line_thickness or round(0.001 * max(img.shape[0:2])) + 1  # line thickness
    color = color or [random.randint(0, 255) for _ in range(3)]
    c1, c2 = (int(x[0]), int(x[1])), (int(x[2]), int(x[3]))
    cv2.rectangle(img, c1, c2, color, thickness=tl)
    if label:
        tf = max(tl - 1, 1)  # font thickness
        t_size = cv2.getTextSize(label, 0, fontScale=tl / 3, thickness=tf)[0]
        c2 = c1[0] + t_size[0], c1[1] - t_size[1] - 3
        cv2.rectangle(img, c1, c2, color, -1)  # filled
        cv2.putText(img, label, (c1[0], c1[1] - 2), 0, tl / 3, [225, 255, 255], thickness=tf, lineType=cv2.LINE_AA)