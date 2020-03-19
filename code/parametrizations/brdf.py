import numpy as np
import torch
from abc import abstractmethod, abstractstaticmethod

from utils.vectors import inner_product, normalize
from parametrizations.parametrization import Parametrization

class BrdfParametrization(Parametrization):
    @staticmethod
    def _calculate_NdotHs(Ls, Vs, normals):
        """
        Internal function for calculation half-vectors and their inner products
        with the surface normals.

        Inputs:
            Ls              NxLx3 torch.tensor with the directions between
                                the points and the scene lights
            Vs              Nx3 torch.tensor with the directions between
                                the points and the camera
            normals         Nx3 torch.tensor with the surface normals
        
        Outputs:
            Hs              NxLx3 torch.tensor with the normalized half-vectors between
                                viewing and light directions.
            NdotHs          NxLx1 torch.tensor containing the inner products between
                                the surface normals and the view-light half vectors
        """
        N, L = Ls.shape[:2]
        Vs = Vs.view(N, 1, 3)
        Hs = (Ls + Vs)
        Hs = normalize(Hs)
        NdotHs = inner_product(normals.view(-1,1,3), Hs)
        return Hs, NdotHs

    @abstractmethod
    def calculate_rhos(self, Ls, Vs, normals, parameter_dict):
        """
        Calculate the reflectance of a set of scene points.

        Inputs:
            Ls              NxLx3 torch.tensor with the directions between
                                the points and the scene lights
            Vs              Nx3 torch.tensor with the directions between
                                the points and the camera
            normals         Nx3 torch.tensor with the surface normals
            parameter_dict  a dictionary containing:
                diffuse     NxB_d torch.tensor with the diffuse material parameters
                specular    a dictionary for torch.tensors with specular parameters
        
        Outputs:
            rhos            NxLx3 torch.tensor with, for each light and color channel,
                                the fraction of incoming light that gets reflected
                                towards the camera
            NdotHs          NxL torch.tensor containing the inner products between
                                the surface normals and the view-light half vectors
        """
        pass

    @abstractmethod
    def get_parameter_count(self):
        """
        The number of parameters necessary to parametrize a single material.

        Outputs:
            B_d             The number of diffuse parameters required
            B_s_dict        A dictionary with specular parameter names and
                                the number parameters they required
        """
        pass

    @abstractmethod
    def enforce_parameter_bounds(self, parameter_dict):
        """
        Perform Euclidean projection of the parameters onto their feasible domain.
        This is performed in-place on the underlying data of the dictionary elements.

        Inputs:
            parameter_dict  a dictionary containing:
                diffuse     NxB_d torch.tensor with the diffuse material parameters
                specular    NxB_s torch.tensor with the specular material parameters
        """
        pass


class Diffuse(BrdfParametrization):
    def calculate_rhos(self, Ls, Vs, normals, parameters):
        Hs, NdotHs = BrdfParametrization._calculate_NdotHs(Ls, Vs, normals)
        rhos = parameters['diffuse'].view(-1, 1, 3) / np.pi

        return rhos, NdotHs
    
    def get_parameter_count(self):
        return 3, {}

    def enforce_parameter_bounds(self, parameters):
        parameters['diffuse'].data.clamp_(min=0.0)

    def serialize(self):
        pass

    def deserialize(self, *args):
        pass


def Fresnel(NdotLs, p_eta_mat):
    """
    Calculate the Fresnel term, given the inner product between the surface normal
    and the lighting direction.
    The environment dielectrical coefficient is assumed to be 1.

    Inputs:
        NdotLs              NxLx1 torch.tensor with the inner products
        p_eta_mat           Nx1 torch.tensor with the dielectric coefficients of the surface
    """
    p_eta_mat = p_eta_mat.view(-1,1,1)
    cos_thetas_env = NdotLs

    # Snell's law
    # sin_thetas_env = torch.nn.functional.relu(1 - cos_thetas_env ** 2).sqrt()
    # sin_thetas_mat = sin_thetas_in / p_eta_mat
    # cos_thetas_mat = torch.nn.functional.relu(1 - sin_thetas_mat ** 2).sqrt()

    # shortcut, less numerical issues
    cos_thetas_mat = (cos_thetas_env**2 + p_eta_mat**2 - 1).sqrt() / p_eta_mat

    # Fresnel equations for both polarizations
    r_p = (
        p_eta_mat * cos_thetas_env - cos_thetas_mat
    ) / (
        p_eta_mat * cos_thetas_env + cos_thetas_mat
    )
    r_s = (
        cos_thetas_env - p_eta_mat * cos_thetas_mat
    ) / (
        cos_thetas_env + p_eta_mat * cos_thetas_mat
    )
    return (r_p ** 2 + r_s ** 2) / 2


def Beckmann(NdotHs, p_roughness):
    """
    Calculate the Beckman microfacet distribution coefficient, given the 
    inner products between the surface normals and the half vectors and the 
    surface roughness.

    Inputs:
        NdotHs          NxLx3 torch.tensor containing the inner products
        p_roughness     Nx1 torch.tensor containing the surface roughnesses
    
    Outputs:
        Ds              NxLx1 torch.tensor containing the microfacet distributions
    """
    p_roughness = p_roughness.view(-1,1,1)
    cosNH2 = (NdotHs ** 2).clamp_(min=0., max=1.)
    cosNH4 = cosNH2 ** 2
    tanNH2 = (1 - cosNH2) / cosNH2
    p_roughness2 = p_roughness**2
    Ds = (-tanNH2 / p_roughness2).exp() / (p_roughness2 * cosNH4)
    return Ds


def GTR(NdotHs, p_roughness, gamma=1.):
    """
    Calculate the GTR microfacet distribution coefficient,given the 
    inner products between the surface normals and the half vectors and the 
    surface roughness.

    Inputs:
        NdotHs          NxLx3 torch.tensor containing the inner products
        p_roughness     Nx1 torch.tensor containing the surface roughnesses
    
    Outputs:
        Ds              NxLx1 torch.tensor containing the microfacet distributions
    """
    p_roughness = p_roughness.view(-1,1,1)
    cosNH2 = (NdotHs ** 2).clamp_(min=0., max=1.)
    p_roughness2 = p_roughness ** 2
    if gamma == 1.:
        cs = (p_roughness2 - 1) / p_roughness2.log()
        Ds = cs / (1 + (p_roughness2 - 1) * cosNH2)
    else:
        cs = (gamma - 1) * (p_roughness2 - 1) / (1 - p_roughness2 ** (1 - gamma))
        Ds = cs / ((1 + (p_roughness2 - 1) * cosNH2) ** gamma)
    return Ds


def SmithG1(NdotWs, p_roughness):
    """
    Calculate Smith's G1 shadowing function, given the relevant inner product
    and the inner product between the viewing vector and the view-light half vector,
    as well as the surface roughness.

    Inputs:
        NdotWs          NxLx3 torch.tensor containing inner products
        VdotHs          NxLx3 torch.tensor containing inner products
        p_roughness     Nx1 torch.tensor containing the surface roughnesses
    
    Outputs:
        Gs              NxLx1 torch.tensor containing the shadowing values
    """
    # if any of the cosines are negative, then this clamping will eventually
    # result in zero shadow-masking coefficient terms
    cos_thetas = NdotWs.clamp_(min=0.0, max=1.0)
    sin_thetas = (1 - cos_thetas**2).sqrt()
    cot_thetas = cos_thetas / sin_thetas
    prelims = cot_thetas / p_roughness.view(-1,1,1)
    prelims2 = prelims**2

    Gs = (3.535 * prelims + 2.181 * prelims2) / (1 + 2.276 * prelims + 2.577 * prelims2)

    # if sin_thetas == 0 -> fix to 1.0 (no shadowing)
    Gs[sin_thetas == 0] = 1.0
    # the above function turns around the wrong way at this point
    Gs[prelims >= 1.6] = 1.0

    return Gs


class CookTorrance(BrdfParametrization):
    def calculate_rhos(self, Ls, Vs, normals, parameters):
        Hs, NdotHs = BrdfParametrization._calculate_NdotHs(Ls, Vs, normals)
        Vs = Vs.view(-1,1,3)
        normals = normals.view(-1,1,3)
        NdotLs = inner_product(normals, Ls)
        NdotVs = inner_product(normals, Vs)
        VdotHs = inner_product(Vs, Hs)

        p_diffuse = parameters['diffuse']
        p_specular = parameters['specular']['albedo']
        # somewhat non-standard, we parametrize roughness as its square root
        # this yields better resolution around zero
        p_roughness = parameters['specular']['roughness'] ** 2

        # fresnel term -- optional
        if 'eta' in parameters['specular']:
            p_eta = parameters['specular']['eta']
            Fs = Fresnel(VdotHs, p_eta)
        else:
            Fs = 1.

        # microfacet distribution
        Ds = GTR(NdotHs, p_roughness)
        # Smith's shadow-masking function
        Gs = SmithG1(NdotLs, p_roughness) * SmithG1(NdotVs, p_roughness)

        CTs = p_specular.view(-1,1,3) * (Fs * Ds * Gs) / (4 * np.pi * NdotLs * NdotVs)

        # guard against bad denominators
        CTs[CTs != CTs] = 0

        rhos = p_diffuse.view(-1,1,3) / np.pi + CTs

        return rhos, NdotHs
    
    def get_parameter_count(self):
        return 3, {'albedo': 3, 'roughness': 1, 'eta': 1}

    def enforce_parameter_bounds(self, parameters):
        params['diffuse'].data.clamp_(min=0.0)
        params['specular']['albedo'].data.clamp_(min=0.0)
        params['specular']['roughness'].data.clamp_(min=1e-6, max=1 - 1e-6)
        if 'eta' in params['specular']:
            params['specular']['eta'].data.clamp_(min=1.0001, max=2.999)

    def serialize(self):
        pass

    def deserialize(self, *args):
        pass


class CookTorranceF1(BrdfParametrization):
    def calculate_rhos(self, Ls, Vs, normals, parameters):
        return CookTorrance.calculate_rhos(
            Ls,
            Vs,
            normals,
        )
    
    def get_parameter_count(self):
        return 3, {'albedo': 3, 'roughness': 1}

    def enforce_parameter_bounds(self, parameters):
        CookTorrance.enforce_parameter_bounds(parameters)

    def serialize(self):
        pass

    def deserialize(self, *args):
        pass


def BrdfParametrizationFactory(name):
    valid_dict = {
        "diffuse": Diffuse,
        "cook torrance": CookTorrance,
        "cook torrance F1": CookTorranceF1,
    }
    if name in valid_dict:
        return valid_dict[name]
    else:
        error("BRDF parametrization '%s' is not supported." % name)