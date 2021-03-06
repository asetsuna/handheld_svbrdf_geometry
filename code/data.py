"""
Copyright (c) 2020 Autonomous Vision Group (AVG), Max Planck Institute for Intelligent Systems, Tuebingen, Germany

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from abc import ABC, abstractmethod
import os
from glob import glob
import numpy as np
import cv2
import torch
import pickle
import json
from functools import lru_cache

import general_settings
from utils.logging import log, error, log_singleton

class ImageWrapper:
    """
    Wrapper around an image file and linked information.
    Wraps all relevant metadata, as it is stored on the file system.
    """

    @staticmethod
    def load_metadata(filename):
        with open(filename.replace("RGB_undistorted", "RAW"), "rt") as fh:
            metadata = json.load(fh)
        return metadata

    def __init__(
        self,
        view_number,
        image_file,
        crop,
        intrinsics,
        extrinsics,
        vignetting,
        is_val_view,
        is_ctr_view,
        light_info,
        down_sample=1,
        reup_sample=1,
        lazy=False,
        device="cuda:0",
    ):
        self.view_number = view_number
        self.image_file = image_file
        self._image = None
        self.crop = crop
        self.original_intrinsics = intrinsics
        self.original_extrinsics = extrinsics
        self.original_vignetting = vignetting
        self.is_val_view = is_val_view
        self.is_ctr_view = is_ctr_view
        self.light_info = light_info
        self.down_sample = down_sample
        self.reup_sample = reup_sample
        self.lazy = lazy
        self.device = torch.device(device)

        self.meta_data = None
        self._K = None
        self._image = None
        # self._saturation_mask = None

    def get_intrinsics(self):
        """
        Get the intrinsics for this observation.
        This incorporates any cropping and rescaling.
        """
        if self._K is None:
            K = self.original_intrinsics.clone()
            if self.crop is not None:
                K[:2,2] -= torch.tensor(self.crop[:,0], device=K.device, dtype=K.dtype)
            K[:2] *= self.reup_sample / self.down_sample
            self._K = K
        return self._K

    def get_image(self):
        """
        Get the image for this observation.
        This incorporates any cropping and rescaling.
        """
        if self._image is None:
            image_data = np.load(self.image_file)
            if not isinstance(image_data, np.ndarray):
                image_data = image_data['arr_0']
            self.meta_data = ImageWrapper.load_metadata(self.image_file+".meta")
            exposure_time = self.meta_data['exposure_time_us'] * 1e-6
            dark_level = float(self.meta_data['black_level'])
            # saturation_mask = image_data.max(axis=2) >= 4094
            image_data = np.clip((image_data.astype(np.float32) - dark_level),
                                a_min=0.0, a_max=None) / exposure_time
            if self.original_vignetting is not None:
                image_data = image_data / self.original_vignetting
            if self.crop is not None:
                image_data = image_data[
                    self.crop[1,0]:self.crop[1,1],
                    self.crop[0,0]:self.crop[0,1]
                ]
                # saturation_mask = saturation_mask[
                #     self.crop[1,0]:self.crop[1,1],
                #     self.crop[0,0]:self.crop[0,1]
                # ]
            if self.down_sample is not None:
                image_data = cv2.resize(
                    image_data,
                    dsize=None,
                    fx=1./self.down_sample,
                    fy=1./self.down_sample,
                    interpolation=cv2.INTER_AREA
                )
                # saturation_mask =  cv2.resize(
                #     saturation_mask,
                #     dsize=None,
                #     fx=1./self.down_sample,
                #     fy=1./self.down_sample,
                #     interpolation=cv2.INTER_AREA
                # )
            if self.reup_sample is not None:
                image_data = cv2.resize(
                    image_data,
                    dsize=None,
                    fx=self.reup_sample,
                    fy=self.reup_sample,
                    interpolation=cv2.INTER_CUBIC
                )
                # saturation_mask =  cv2.resize(
                #     saturation_mask,
                #     dsize=None,
                #     fx=self.reup_sample,
                #     fy=self.reup_sample,
                #     interpolation=cv2.INTER_CUBIC
                # )
            image = torch.tensor(np.transpose(image_data, (2,0,1)), dtype=torch.float32, device=self.device)
            # saturation_mask = torch.tensor(saturation_mask, dtype=torch.float32, device=self.device)
            if not self.lazy:
                self._image = image
                # self._saturation_mask = saturation_mask
        else:
            image = self._image
            # saturation_mask = self._saturation_mask

        return image#, saturation_mask

class ImageAdapter(ABC):
    """
    Adapter for the file system, responsible from loading images and relevant metadata,
    and formatting them into ImageWrappers.
    """

    @staticmethod
    def load_depth_and_bbox(depth_dir, view, depth_scale, device):
        """
        Internal helper function trying to load depth from file, as well as bounding box information.
        """
        base_filename = os.path.join(depth_dir, "%05d" % view)
        if os.path.exists(base_filename + ".npz"):
            npz_dict = np.load(base_filename + ".npz")
            if 'arr_0' in npz_dict:
                depth = npz_dict['arr_0']
                crop = None
            else:
                depth = npz_dict['depth']
                crop = npz_dict['bbox']
        elif os.path.exists(base_filename + ".npy"):
            depth = np.load(base_filename + ".npy")
            crop = None
        else:
            depth = None
            crop = None
        if depth is not None:
            depth = torch.tensor(depth * depth_scale, dtype=torch.float32, device=device)
        if crop is None:
            crop_files = glob(base_filename + "_bbox*")
            if len(crop_files) == 1:
                crop = np.load(crop_files[0])
            elif len(crop_files) > 1:
                error("Crop file base '%s_bbox' matches multiple files" % base_filename)
            
        return depth, crop

    @staticmethod
    def load_bbox(depth_dir, view):
        """
        Internal helper function trying to load bounding box information from file.
        """
        base_filename = os.path.join(depth_dir, "%05d" % view)
        if os.path.exists(base_filename + ".npz"):
            npz_dict = np.load(base_filename + ".npz")
            if 'bbox' in npz_dict:
                crop = npz_dict['bbox']
            else:
                crop = None
        else:
            crop = None
        if crop is None:
            crop_files = glob(base_filename + "_bbox*")
            if len(crop_files) == 1:
                crop = np.load(crop_files[0])
            elif len(crop_files) > 1:
                error("Crop file base '%s_bbox' matches multiple files" % base_filename)
        return crop

    @staticmethod
    def load_pose(color_dir, view, depth_scale, device):
        """
        Internal helper function to load camera pose information from file.
        """
        pose_file = os.path.join(color_dir, '%05d.pose' % view)
        if os.path.exists(pose_file):
            with open(pose_file, "rb") as fh:
                pose = pickle.load(fh)
            pose[:3,3:] *= depth_scale
            pose = torch.tensor(
                np.concatenate(
                    (pose, np.array([0, 0, 0, 1]).reshape(1, 4)),
                    axis=0
                ),
                dtype=torch.float32,
                device=device,
            )
        else:
            error("Pose file '%s' does not exist." % pose_file)
        return pose

    def get_center_index(self):
        """
        Get the index (in the images array) of the reference view, if it is present as an observation.

        Outputs:
            center_index        python int with the index, or None if it is not present
        """
        center_indices = [i for i, image in enumerate(self.images) if image.is_ctr_view]
        if len(center_indices) == 0:
            log_singleton("no_center_view", "The center view is not in the observations")
            return None
        elif len(center_indices) > 1:
            error("Multiple copies of the center view present")
        else:
            return center_indices[0]

    def _get_info_set(self, criterion=lambda x: True):
        """
        Format the image indices and related lighting information as prescribed by general_settings.batch_size.

        Outputs:
            indices, light_infos            python lists containing, in parallel:
                batch_indices               (long) torch.tensor containing indices into the observation list
                batch_light_infos           (long) torch.tensor containing information required for the lighting model  
                                                Typically indices into the light list
        """
        device = torch.device(general_settings.device_name)
        training_indices = []
        training_light_infos = []
        for image_index, image in enumerate(self.images):
            if criterion(image):
                training_indices.append(image_index)
                training_light_infos.append(image.light_info)
        training_indices = torch.tensor(training_indices, dtype=torch.long, device=device)
        training_light_infos = torch.tensor(training_light_infos, dtype=torch.long, device=device)
        if training_light_infos.min() < 0:
            error("Trying to reconstruct an image without a light source.")
        batch_size = general_settings.batch_size
        training_indices = [
            training_indices[i0:min(i0+batch_size, len(training_indices))]
            for i0 in range(0, len(training_indices), batch_size)
        ]
        training_light_infos = [
            training_light_infos[i0:min(i0+batch_size, len(training_light_infos))]
            for i0 in range(0, len(training_light_infos), batch_size)
        ]
        return training_indices, training_light_infos

    @lru_cache(maxsize=1)
    def get_training_info(self):
        return self._get_info_set(criterion=lambda image: not image.is_val_view)

    @lru_cache(maxsize=1)
    def get_testing_info(self):
        return self._get_info_set(criterion=lambda image: image.is_val_view)

    @abstractmethod
    def __init__(self, data_settings):
        pass

def _constant_generator(constant):
    """
    Internal helper generator 
    """
    while True:
        yield constant

class XimeaAdapter(ImageAdapter):
    image_size = [3008,4112]

    def __init__(self, data_settings):
        self.center_view = data_settings['center_view']

        center_view_dictionary = torch.load(os.path.join(
            data_settings['base_input_path'],
            data_settings['object_name'],
            "center_views",
            "views.pkl"
        ))["%05d" % self.center_view]
        self.observation_views = [
            *zip(
                center_view_dictionary["%02d_views" % data_settings['nr_neighbours']],
                _constant_generator(False)
            ),
            *zip(
                center_view_dictionary["val_views"],
                _constant_generator(True)
            ),
        ]

        color_dir = os.path.join(
            data_settings['base_input_path'],
            data_settings['object_name'],
            "RGB_undistorted",
        )

        depth_dir = os.path.join(
            data_settings['base_input_path'],
            data_settings['object_name'],
            data_settings['depth_folder'],
        )
        device = torch.device(general_settings.device_name)

        K_color = torch.tensor(np.load(os.path.join(
            data_settings['calibration_path_geometric'], "stereo_calibration.npz"
        ))['intrinsic_color'], dtype=torch.float32, device=device)
        
        self.center_invRt = ImageAdapter.load_pose(
            color_dir,
            self.center_view,
            data_settings['depth_scale'],
            device
        ).inverse()

        center_K = K_color.clone()
        center_depth, center_crop = ImageAdapter.load_depth_and_bbox(
            depth_dir,
            self.center_view,
            data_settings['depth_scale'],
            device
        )
        if data_settings['manual_center_view_crop'] is not None:
            log("Manually cropping the center view")
            center_crop = data_settings['manual_center_view_crop']
        self.center_crop = center_crop

        if data_settings.get('handmade_mask', None) is not None:
            center_depth = center_depth * (cv2.imread(data_settings['handmade_mask'])[:,:,:1] > 0)

        if center_depth is None:
            log(
                "Warning> missing depth for view %05d. Filled in to the Z=0 plane"
                % self.center_view
            )
            pixels_y, pixels_x = torch.meshgrid([
                torch.arange(0, self.image_size[0]),
                torch.arange(0, self.image_size[1]),
            ])
            pixels = torch.stack(
                [pixels_x, pixels_y, torch.ones_like(pixels_y)],
                dim=2
            ).float().to(device)

            # pixel rays in camera space
            pixels_normed = pixels @ K_color.inverse().T[None]
            center_depth = - self.center_invRt[2,3] / (
                pixels_normed @ self.center_invRt[2:3,:3].T[None]
            ).squeeze()

        if center_crop is not None:
            center_depth = center_depth[
                center_crop[1,0]:center_crop[1,1],
                center_crop[0,0]:center_crop[0,1],
            ]
            center_K[0,2] -= center_crop[0,0]
            center_K[1,2] -= center_crop[1,0]

        if data_settings['center_stride'] > 1:
            stride = data_settings['center_stride']
            center_depth = center_depth[::stride, ::stride]
            center_K[:2] /= stride

        self.center_depth = center_depth
        self.center_invK = center_K.inverse()
        self.depth_unit = data_settings['depth_scale']

        vignetting_dict = np.load(data_settings['vignetting_file'])
        vignetting = np.stack(
            [vignetting_dict[x] for x in ['ratio_0', 'ratio_1', 'ratio_2']],
            axis=2
        )

        # load the light indices
        light_indices = np.loadtxt(os.path.join(
            data_settings['base_input_path'],
            data_settings['object_name'],
            'image2LED.txt'
        )).astype(np.int32)

        self.images = [
            ImageWrapper(
                view_number=view,
                image_file=os.path.join(color_dir, "%05d.npy" % view),
                crop=ImageAdapter.load_bbox(
                    depth_dir, view
                ),
                intrinsics=K_color,
                extrinsics=ImageAdapter.load_pose(
                    color_dir, view, data_settings['depth_scale'], device
                ),
                vignetting=vignetting,
                is_val_view=val_view,
                is_ctr_view= view == self.center_view,
                light_info=light_indices[np.where(light_indices[:,0] == view)[0].item(), 1],
                down_sample=data_settings['input_down_sample'],
                reup_sample=data_settings['input_reup_sample'],
                lazy=data_settings['lazy_image_loading'],
                device=general_settings.device_name,
            )
            for view, val_view in self.observation_views
        ]

def DataAdapterFactory(name):
    if name == "XIMEA":
        return XimeaAdapter
    else:
        error("Data type '%s' is not known.")
