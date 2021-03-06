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

import numpy as np

def export_pointcloud_as_ply(filename, points, normals=None, colors=None):
    """
    Save a set of points, optionally with normals and colors, to a .ply file.
    The file is binary.

    Inputs:
        filename            string containing the output path
        points              Nx3 torch.tensor containing XYZ locations
        [normals]           Nx3 torch.tensor containing normal vectors
        [colors]            Nx3 torch.tensor containing BGR color information
    """
    dtype = {
        'names': ['x', 'y', 'z', ],
        'formats': ['f4', 'f4', 'f4', ]
    }
    if normals is not None:
        dtype["names"] += ['nx', 'ny', 'nz', ]
        dtype["formats"] += ['f4', 'f4', 'f4', ]
    if colors is not None:
        dtype["names"] += ['red', 'green', 'blue', ]
        dtype["formats"] += ['u1', 'u1', 'u1', ]
    
    points = points.detach().cpu().numpy()
    point_cloud = np.empty(points.shape[0], dtype=dtype)
    point_cloud['x'] = points[:, 0]
    point_cloud['y'] = points[:, 1]
    point_cloud['z'] = points[:, 2]
    if normals is not None:
        normals = normals.detach().cpu().numpy()
        point_cloud['nx'] = normals[:, 0]
        point_cloud['ny'] = normals[:, 1]
        point_cloud['nz'] = normals[:, 2]
    if colors is not None:
        colors = colors.detach().cpu().numpy()
        point_cloud['red'] = colors[:, 2]
        point_cloud['green'] = colors[:, 1]
        point_cloud['blue'] = colors[:, 0]

    with open(filename, "wt") as fh:
        fh.write("ply\n")
        fh.write("format binary_little_endian 1.0\n")
        fh.write("element vertex %d\n" % point_cloud.shape[0])
        fh.write("property float x\n")
        fh.write("property float y\n")
        fh.write("property float z\n")
        if normals is not None:
            fh.write("property float nx\n")
            fh.write("property float ny\n")
            fh.write("property float nz\n")
        if colors is not None:
            fh.write("property uchar red\n")
            fh.write("property uchar green\n")
            fh.write("property uchar blue\n")
        fh.write("end_header\n")
        point_cloud.tofile(fh)
