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

import torch
from abc import abstractmethod
from parametrizations.parametrization import Parametrization

from utils.logging import error

from functools import lru_cache

class MaterialParametrization(Parametrization):
    """
    Parametrization representing the material properties of the scene points.
    These material properties are to be converted to reflectance coefficients
    with the help of the relevant BrdfParametrization.
    """

    def __init__(self, brdf_parametrization=None):
        super().__init__()
        if brdf_parametrization is None:
            error("MaterialParametrization should get created with a BrdfParametrization")
        self.brdf_parametrization = brdf_parametrization

    @abstractmethod
    def initialize(nr_points, diffuse_materials, device):
        """
        Create a MaterialParametrization object for the given number of points
        and using the given BrdfParametrization on a given device. May have
        additional optional arguments for specific subclasses.
        """
        pass

    @abstractmethod
    def get_brdf_parameters(self):
        """
        Get the actual represented materials for every point.

        Outputs:
            parameter_dict  a dictionary containing:
                diffuse     NxB_d torch.tensor with the diffuse material parameters
                specular    a dictionary for torch.tensors with specular parameters
                    name        NxB_s torch.tensor with the relevant specular values
        """
        pass

    @abstractmethod
    def enforce_parameter_bounds(self):
        """
        Enforce parameter bounds on both the underlying brdf parameters (by 
        calling the relevant BrdfParametrization object) and any auxiliary
        parameters by the actual MaterialParametrization class by euclidean
        projection onto the feasible set.
        
        Acts in-place on the .data elements of all tensors.
        """
        pass


def detach_and_clone_dict_recursive(dictionary):
    """
    Create a new dictionary with the same elements, detached as required.
    The only nested collection that is supported is a dict.
    """
    if not isinstance(dictionary, dict):
        error("detach_and_clone_dict_recursive acting on neither a Tensor nor a dict.")
    new_dictionary = dict([
        [
            key,
            value.detach().clone() if isinstance(value, torch.Tensor)
            else detach_and_clone_dict_recursive(value)
        ]
        for key, value in dictionary.items()
    ])
    return new_dictionary

def attach_dict_recursive(dictionary):
    """
    Create a new dictionary with the same elements, all as torch.nn.Parameters.
    The only nested collection that is supported is a dict.
    """
    if not isinstance(dictionary, dict):
        error("attach_dict_recursive acting on neither a Tensor nor a dict.")
    new_dictionary = dict([
        [
            key,
            torch.nn.Parameter(value) if isinstance(value, torch.Tensor)
            else attach_dict_recursive(value)
        ]
        for key, value in dictionary.items()
    ])
    return new_dictionary


class BaseSpecularMaterials(MaterialParametrization):
    """
    A MaterialParametrization subclass that has distinct diffuse albedos for all
    scene points, but parametrizes specular behavior as a mix of multiple base
    behaviors.
    """
    
    def initialize(self, nr_points, diffuse_materials, device, nr_bases=2):
        B_d, B_s_dict = self.brdf_parametrization.get_parameter_count()
        self.brdf_parameters = {
            'diffuse': torch.nn.Parameter(diffuse_materials.detach().to(device)),
            'specular': dict([
                    [
                        name,
                        torch.nn.Parameter(torch.ones(
                            (nr_bases, B_s),
                            device=device)
                        )
                    ]
                    for name, B_s in B_s_dict.items()
            ])
        }
        self.base_weights = torch.nn.Parameter(torch.ones(nr_points, nr_bases, device=device) / nr_bases)
    
    @lru_cache(maxsize=1)
    def get_brdf_parameters(self):
        return {
            "diffuse": self.brdf_parameters['diffuse'],
            "specular": dict([
                [
                    name,
                    self.base_weights @ base_parameters
                ]
                for name, base_parameters in self.brdf_parameters['specular'].items()
            ])
        }
    
    def parameter_info(self):
        return {
            "diffuse_materials": [self.brdf_parameters['diffuse'], 1e-2, lambda x: {"diffuse_albedo": x.mean(dim=0, keepdim=True)}],
            "specular_weights": [self.base_weights, 5e-2, lambda x: - (x * (x + (x == 0).float()).log()).sum(dim=1).mean()],
            "specular_materials": [list(self.brdf_parameters['specular'].values()), 1e-2, lambda x: {"specular_albedo": x[0].detach().clone(), "roughness": x[1].detach().clone()}]
        }

    def enforce_parameter_bounds(self):
        self.base_weights.data.clamp_(min=0.,max=1.)
        self.base_weights.data.div_(self.base_weights.sum(dim=-1, keepdim=True))
        self.brdf_parametrization.enforce_brdf_parameter_bounds(self.brdf_parameters)

    def serialize(self):
        return self.base_weights.detach().clone(), detach_and_clone_dict_recursive(self.brdf_parameters)
    
    def deserialize(self, *args):
        self.base_weights = torch.nn.Parameter(args[0])
        self.brdf_parameters = attach_dict_recursive(args[1])


def MaterialParametrizationFactory(name):
    valid_dict = {
        "base specular materials": BaseSpecularMaterials,
    }
    if name in valid_dict:
        return valid_dict[name]
    else:
        error("Material parametrization '%s' is not supported." % name)
