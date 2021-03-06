#!/usr/bin/python27
#coding:utf-8
from __future__ import division
import os
import random
import tensorflow as tf

class DataLoader(object):
    def __init__(self, 
                 dataset_dir=None, 
                 batch_size=None,
                 img_height=None, 
                 img_width=None, 
                 num_source=None, 
                 num_scales=None):
        self.dataset_dir = dataset_dir
        self.batch_size = batch_size
        self.img_height = img_height
        self.img_width = img_width
        self.num_source = num_source
        self.num_scales = num_scales

    def load_train_batch(self):
        """Load a batch of training instances.
        """
        seed = random.randint(0, 2**31 - 1)  #integer creating in a particular scope
        # Load the list of training files into queues
        file_list = self.format_file_list(self.dataset_dir, 'train') #read resulting/formatted/data train.txt
        # print('train_file_list:',file_list)
        image_paths_queue = tf.train.string_input_producer(
            file_list['image_file_list'], 
            seed=seed, 
            shuffle=True)   #True stands for random sequences
        cam_paths_queue = tf.train.string_input_producer(
            file_list['cam_file_list'], 
            seed=seed, 
            shuffle=True)
        self.steps_per_epoch = int(
            len(file_list['image_file_list'])//self.batch_size)
        # print('self.steps_per_epoch:',self.steps_per_epoch)

        # Load images
        img_reader = tf.WholeFileReader() #reader
        _, image_contents = img_reader.read(image_paths_queue) #reader
        image_seq = tf.image.decode_jpeg(image_contents)       #tensor
        tgt_image, src_image_stack = \
            self.unpack_image_sequence(
                image_seq, self.img_height, self.img_width, self.num_source)

        # print('tgt_image.shape',tgt_image.shape)                # shape=(128, 416, 3)
        # print('src_image_stack',src_image_stack.shape)          # shape=(128, 416, 6)

        # Load camera intrinsics
        cam_reader = tf.TextLineReader()
        _, raw_cam_contents = cam_reader.read(cam_paths_queue)
        rec_def = []
        for i in range(9):
            rec_def.append([1.])
        raw_cam_vec = tf.decode_csv(raw_cam_contents, 
                                    record_defaults=rec_def)  #list
        raw_cam_vec = tf.stack(raw_cam_vec)
        # raw_cam_vec = tf.Print(raw_cam_vec,[raw_cam_vec],message='raw_cam_vec')       shape(9,)
        intrinsics = tf.reshape(raw_cam_vec, [3, 3])
        # intrinsics = tf.Print(intrinsics,[intrinsics],message='intrinsics before train.batch:')  #scale  (3,3)
        # print('intrinsics:',intrinsics)

        # Form training batches
        src_image_stack, tgt_image, intrinsics = \
                tf.train.batch([src_image_stack, tgt_image, intrinsics], 
                               batch_size=self.batch_size)

        # intrinsics = tf.Print(intrinsics, [intrinsics], message='intrinsics after train.batch:')
        # print('intrinsics after train batch:', intrinsics.shape)        #shape(4,3,3)
        # print('tgt_image.shape', tgt_image.shape)                       #shape(4,128,416,3)
        # print('src_image_stack', src_image_stack.shape)                 #shape(4,128,416,6)

        # Data augmentation
        image_all = tf.concat([tgt_image, src_image_stack], axis=3)      #shape(4,128,416,9)
        image_all, intrinsics = self.data_augmentation(
            image_all, intrinsics, self.img_height, self.img_width)
        # intrinsics = tf.Print(intrinsics, [intrinsics], message='intrinsics after augmentation:')   #shape(4,3,3)
        # intrinsics = tf.Print(intrinsics, [intrinsics], message='intrinsics.after:')
        tgt_image = image_all[:, :, :, :3]                               #shape(4,128,416,3)
        # print('tgt_image.shape:',tgt_image.shape)
        src_image_stack = image_all[:, :, :, 3:]                         #shape(4,128,416,6)
        # print('src_image_stack.shape:', src_image_stack.shape)
        intrinsics = self.get_multi_scale_intrinsics(                    #tensor shape(4,4,3,3)
            intrinsics, self.num_scales)
        # intrinsics = tf.Print(intrinsics,[intrinsics],message='intrinsics after get_multi_scale:')
        # print('intrinsics.shape:',intrinsics.shape)
        return tgt_image, src_image_stack, intrinsics
#生成内参矩阵的函数
    def make_intrinsics_matrix(self, fx, fy, cx, cy):
        # Assumes batch input
        batch_size = fx.get_shape().as_list()[0]
        zeros = tf.zeros_like(fx)
        r1 = tf.stack([fx, zeros, cx], axis=1)
        r2 = tf.stack([zeros, fy, cy], axis=1)
        r3 = tf.constant([0.,0.,1.], shape=[1, 3])
        r3 = tf.tile(r3, [batch_size, 1])
        intrinsics = tf.stack([r1, r2, r3], axis=1)
        return intrinsics

    def data_augmentation(self, im, intrinsics, out_h, out_w):
        # Random scaling
        def random_scaling(im, intrinsics):
            batch_size, in_h, in_w, _ = im.get_shape().as_list()
            scaling = tf.random_uniform([2], 1, 1.15)                   #shape(2,),(1,1.5)
            x_scaling = scaling[0]
            y_scaling = scaling[1]
            out_h = tf.cast(in_h * y_scaling, dtype=tf.int32)
            out_w = tf.cast(in_w * x_scaling, dtype=tf.int32)
            im = tf.image.resize_area(im, [out_h, out_w])
            fx = intrinsics[:,0,0] * x_scaling
            fy = intrinsics[:,1,1] * y_scaling
            cx = intrinsics[:,0,2] * x_scaling
            cy = intrinsics[:,1,2] * y_scaling
            intrinsics = self.make_intrinsics_matrix(fx, fy, cx, cy)
            # intrinsics = tf.Print(intrinsics, [intrinsics], message='intrinsics.after:')
            return im, intrinsics
        #FIXME：此处的相机内参矩阵需要乘不同的scaling,不过是随机scaling的。

        # Random cropping　随机剪切
        def random_cropping(im, intrinsics, out_h, out_w):
            # batch_size, in_h, in_w, _ = im.get_shape().as_list()
            batch_size, in_h, in_w, _ = tf.unstack(tf.shape(im))
            # in_h = tf.Print(in_h,[in_h],message='in_h')                 #FIXME：此处输入的image大小已经发生变化
            # in_w = tf.Print(in_w, [in_w], message='in_w')
            offset_y = tf.random_uniform([1], 0, in_h - out_h + 1, dtype=tf.int32)[0]
            # offset_y = tf.Print(offset_y, [offset_y], message='offset_y:')
            offset_x = tf.random_uniform([1], 0, in_w - out_w + 1, dtype=tf.int32)[0]
            #FIXME：offset_y,offset_x 不是[0,1]之间的数
            im = tf.image.crop_to_bounding_box(
                im, offset_y, offset_x, out_h, out_w) # FIXME:图像的裁剪,图像的左上角位于offset_height,offset_width,中心改变
            fx = intrinsics[:,0,0]
            fy = intrinsics[:,1,1]
            cx = intrinsics[:,0,2] - tf.cast(offset_x, dtype=tf.float32) #FIXME：此处减的原因：cropping 使得原点位置变化，有平移变化
            cy = intrinsics[:,1,2] - tf.cast(offset_y, dtype=tf.float32)
            intrinsics = self.make_intrinsics_matrix(fx, fy, cx, cy)
            return im, intrinsics
        im, intrinsics = random_scaling(im, intrinsics)
        im, intrinsics = random_cropping(im, intrinsics, out_h, out_w)
        im = tf.cast(im, dtype=tf.uint8)
        return im, intrinsics
#格式化数据,all_list 包括.jpg和cam.txt
    def format_file_list(self, data_root, split):
        with open(data_root + '/%s.txt' % split, 'r') as f: #一行一行地打开,目录应该在--data_root=resulting/formatted/data_TUM/
             frames=f.readlines()
        subfolders = [x.split(' ')[0] for x in frames]
        frame_ids = [x.split(' ')[1][:-1] for x in frames]          #[:-1]操作是去掉'\n'

        image_file_list = [os.path.join(data_root, subfolders[i], 
            frame_ids[i] + '.jpg') for i in range(len(frames))]
        cam_file_list = [os.path.join(data_root, subfolders[i], 
            frame_ids[i] + '_cam.txt') for i in range(len(frames))]
        all_list = {}
        all_list['image_file_list'] = image_file_list
        all_list['cam_file_list'] = cam_file_list
        return all_list

#取出image_sequence
    def unpack_image_sequence(self, image_seq, img_height, img_width, num_source):
        # Assuming the center image is the target frame
        tgt_start_idx = int(img_width * (num_source//2))   #416
        tgt_image = tf.slice(image_seq, 
                             [0, tgt_start_idx, 0], 
                             [-1, img_width, -1])
        # tgt_image = tf.Print(tgt_image,[tgt_image],message='tgt_image',summarize=20)
        # Source frames before the target frame
        src_image_1 = tf.slice(image_seq, 
                               [0, 0, 0], 
                               [-1, int(img_width * (num_source//2)), -1])          #shape(?,416,?)
        # src_image_1 = tf.Print(src_image_1,[src_image_1.shape],message='src_image_1.shape')
        # print('src_image_1.shape',src_image_1.shape)
        # Source frames after the target frame
        src_image_2 = tf.slice(image_seq, 
                               [0, int(tgt_start_idx + img_width), 0], 
                               [-1, int(img_width * (num_source//2)), -1])
        # src_image_2=tf.Print(src_image_2,[src_image_2.shape[0]],message='src_image_2.shape')                         #shape(?,416,?)
        src_image_seq = tf.concat([src_image_1, src_image_2], axis=1)   #(,416*2,)
        # print('src_image_seq.shape',src_image_seq.shape)
        # Stack source frames along the color channels (i.e. [H, W, N*3])
        src_image_stack = tf.concat([tf.slice(src_image_seq, 
                                    [0, i*img_width, 0], 
                                    [-1, img_width, -1]) 
                                    for i in range(num_source)], axis=2)   #在axis=2 即color channels上concat
        # print('src_image_stack.shape',src_image_stack.shape)
        src_image_stack.set_shape([img_height,
                                   img_width, 
                                   num_source * 3])  # 此处乘以３是因为每张图片都是[image_height,image_width,3],在axis=2上连接，就是6,因此 shape(128,416,6)
        # print('src_image_stack.set_shape', src_image_stack.shape)     #(128,416,6)
        tgt_image.set_shape([img_height, img_width, 3])   #shape(128,416,3)
        return tgt_image, src_image_stack

#FIXME:
    def batch_unpack_image_sequence(self, image_seq, img_height, img_width, num_source):
        # Assuming the center image is the target frame
        tgt_start_idx = int(img_width * (num_source//2))
        tgt_image = tf.slice(image_seq,  # shape(1,128,416,3)
                             [0, 0, tgt_start_idx, 0], 
                             [-1, -1, img_width, -1])
        # Source frames before the target frame
        src_image_1 = tf.slice(image_seq,  # shape(1,128,416,3)
                               [0, 0, 0, 0], 
                               [-1, -1, int(img_width * (num_source//2)), -1])
        # Source frames after the target frame
        src_image_2 = tf.slice(image_seq,  # # shape(1,128,416,3)
                               [0, 0, int(tgt_start_idx + img_width), 0], 
                               [-1, -1, int(img_width * (num_source//2)), -1])
        src_image_seq = tf.concat([src_image_1, src_image_2], axis=2)           #  shape(1,128,832,3)
        # Stack source frames along the color channels (i.e. [B, H, W, N*3])
        src_image_stack = tf.concat([tf.slice(src_image_seq,                    #   shape(1,128,416,6)
                                    [0, 0, i*img_width, 0], 
                                    [-1, -1, img_width, -1]) 
                                    for i in range(num_source)], axis=3)
        return tgt_image, src_image_stack

    def get_multi_scale_intrinsics(self, intrinsics, num_scales):
        intrinsics_mscale = []
        # Scale the intrinsics accordingly for each scale
        for s in range(num_scales): # FIXME：num_scales=4,此处针对不同的scale,对intrinsics加一个scale尺度。
            fx = intrinsics[:,0,0]/(2 ** s)
            fy = intrinsics[:,1,1]/(2 ** s)
            cx = intrinsics[:,0,2]/(2 ** s)
            cy = intrinsics[:,1,2]/(2 ** s)
            intrinsics_mscale.append(
                self.make_intrinsics_matrix(fx, fy, cx, cy))
        intrinsics_mscale = tf.stack(intrinsics_mscale, axis=1)

        return intrinsics_mscale


    # sfm = SfMLearner()




