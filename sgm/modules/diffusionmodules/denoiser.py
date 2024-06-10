from typing import Dict, Union, Optional

import torch
import torch.nn as nn
import os

from ...util import append_dims, instantiate_from_config
from .denoiser_scaling import DenoiserScaling
from .discretizer import Discretization
from einops import repeat, rearrange

from ..attention import CrossAttention


class Denoiser(nn.Module):
    timestep_counter = 0
    def __init__(self, scaling_config: Dict, save_attention_weights: bool = False, blend_feature_maps: bool = False):
        super().__init__()

        self.scaling: DenoiserScaling = instantiate_from_config(scaling_config)
        self.save_attention_weights = save_attention_weights
        self.blend_feature_maps = blend_feature_maps
    def possibly_quantize_sigma(self, sigma: torch.Tensor) -> torch.Tensor:
        return sigma

    def possibly_quantize_c_noise(self, c_noise: torch.Tensor) -> torch.Tensor:
        return c_noise

    def forward(
        self,
        network: nn.Module,
        input: torch.Tensor, # [BF,4,72,72]
        sigma: torch.Tensor,
        cond: Dict, #concat:[BF,4,72,72], crossattn:[BF,1,1024], vector:[BF,1280]
        **additional_model_inputs,
    ) -> torch.Tensor:
        # print()
        # print("[denoiser forward]")
        # print(input.shape, input.mean(), input.std())
        # for k, v in cond.items():
        #     if isinstance(v, torch.Tensor):
        #         print(k, v.shape, v.mean(), v.std())

        # print("sigma")
        # print(sigma.shape, sigma)

        # print("[denoiser forward], uc")
        # print(input[:21].shape, input[:21].mean(), input[:21].std())
        # print(sigma[:21].shape, sigma[:21].mean(), sigma[:21].std())
        # for k, v in cond.items():
        #     if isinstance(v, torch.Tensor):
        #         print(k, v[:21].shape, v[:21].mean(), v[:21].std())
        
        # print("[denoiser forward], c")
        # print("input", input[21:].shape, input[21:].mean(), input[21:].std())
        # print("cond")
        # for k, v in cond.items():
        #     if isinstance(v, torch.Tensor):
        #         print(k, v[21:].shape, v[21:].mean(), v[21:].std())
        # print("sigma", sigma[21:].shape, sigma[21:].mean(), sigma[21:].std())

        sigma = self.possibly_quantize_sigma(sigma)
        sigma_shape = sigma.shape
        sigma = append_dims(sigma, input.ndim)
        c_skip, c_out, c_in, c_noise = self.scaling(sigma)
        c_noise = self.possibly_quantize_c_noise(c_noise.reshape(sigma_shape))
        # print()
        # print("network input")
        # print(input.shape, c_in.shape, c_noise.shape, c_out.shape, c_skip.shape)
        
        # dataset_path = str(self.image_path).split("/")[0]
        image_path = str(self.image_path).split("/")[1] #TODO: adjust path
        
        # Save the attention weights
        if self.blend_feature_maps:
            for name, module in network.named_modules():
                if isinstance(module, CrossAttention):
                    if 'time' in name: #TODO: cleaner code..
                        continue
                    # breakpoint()
                    module.previous_feature_map = torch.load(f"featuremaps/{image_path}/{Denoiser.timestep_counter}/{name}.pt") #TODO: bad coding, we are making an attribute outside the class
        output = network(input * c_in, c_noise, cond, **additional_model_inputs)
        print(f"denoising loop for timestep {Denoiser.timestep_counter}")
        
        
        if self.save_attention_weights:
            os.makedirs(f"featuremaps/{image_path}/{Denoiser.timestep_counter}", exist_ok=True)
            print(f"Saving attention weights for featuremaps/{image_path}/{Denoiser.timestep_counter}")
            for name, module in network.named_modules():
                if isinstance(module, CrossAttention) and module.attention_score is not None:
                    # print(f"Saving attention weights for {name}")
                    # print(f"Attention score shape: {module.attention_score.shape}")
                    # import time
                    # start_time = time.time()
                    torch.save(module.attention_score, f"featuremaps/{image_path}/{Denoiser.timestep_counter}/{name}.pt")
                    # end_time = time.time()
                    # print(f"Time taken to save attention weights: {end_time - start_time}")
        
        
        Denoiser.timestep_counter += 1
        if Denoiser.timestep_counter == 50:
            Denoiser.timestep_counter = 0
        return (
            output * c_out+ input * c_skip
        )
    

class SV3DDenoiser(Denoiser):
    def __init__(self, scaling_config: Dict, blend_feature_maps: bool = False):
        super().__init__(scaling_config, blend_feature_maps=blend_feature_maps)

    def forward(
        self,
        network: nn.Module,
        input: torch.Tensor, # [B,F,C,72,72]
        sigma: torch.Tensor,
        cond: Dict, #concat:[B,4,72,72], crossattn:[B,1,1024], vector:[B,1280]
        **additional_model_inputs,
    ) -> torch.Tensor:
        # print()

        b, f = input.shape[:2]
        input = rearrange(input, "b f ... -> (b f) ...")

        for k in ["crossattn", "concat"]:
            cond[k] = repeat(cond[k], "b ... -> b f ...", f=f)
            cond[k] = rearrange(cond[k], "b f ... -> (b f) ...", f=f)

        additional_model_inputs["image_only_indicator"] = torch.zeros((b,f)).to(input.device, input.dtype)
        additional_model_inputs["num_video_frames"] = f

        # print("[SV3D denoiser forward]")
        # print(input.shape, input.mean(), input.std())
        # for k, v in cond.items():
        #     if isinstance(v, torch.Tensor):
        #         print(k, v.shape, v.mean(), v.std())
        # print("sigma", sigma.shape, sigma)

        sigma = self.possibly_quantize_sigma(sigma)
        sigma_shape = sigma.shape
        sigma = append_dims(sigma, input.ndim)
        c_skip, c_out, c_in, c_noise = self.scaling(sigma)
        c_noise = self.possibly_quantize_c_noise(c_noise.reshape(sigma_shape))
        if self.blend_feature_maps:
            for name, module in network.named_modules():
                if isinstance(module, CrossAttention):
                    if 'time' in name: #TODO: cleaner code..
                        continue
                    video_path = self.video_path[0]
                    data_name = video_path.split("/")[1]
                    module.previous_feature_map = torch.load(f"featuremaps/{data_name}/{self.timestep}/{name}.pt") #TODO: bad coding, we are making an attribute outside the class
        
        input.requires_grad = True
        network_output = network(input * c_in, c_noise, cond, **additional_model_inputs)
        # print(network_output.shape, input.shape)
        
        # For debugging, print out the weights
        for name, module in network.named_modules():
            if isinstance(module, CrossAttention) and 'time' not in name:
                # print("prev_feature_mixin")
                # print(module.prev_feature_mixin.weight)
                # print("curr_feature_mixin")
                # print(module.curr_feature_mixin.weight)
                print("blend")
                print(module.blend)
        
        # for name, module in network.named_modules():
        #     if isinstance(module, CrossAttention) and 'time' not in name:
        #         # print("prev_feature_mixin")
        #         # print(module.prev_feature_mixin.weight)
        #         # print("curr_feature_mixin")
        #         # print(module.curr_feature_mixin.weight)
        #         print(module.to_q.weight)

        return network_output * c_out + input * c_skip
    
        return (
            network(input * c_in, c_noise, cond, **additional_model_inputs) * c_out
            + input * c_skip
        )


class DiscreteDenoiser(Denoiser):
    def __init__(
        self,
        scaling_config: Dict,
        num_idx: int,
        discretization_config: Dict,
        do_append_zero: bool = False,
        quantize_c_noise: bool = True,
        flip: bool = True,
    ):
        super().__init__(scaling_config)
        self.discretization: Discretization = instantiate_from_config(
            discretization_config
        )
        sigmas = self.discretization(num_idx, do_append_zero=do_append_zero, flip=flip)
        self.register_buffer("sigmas", sigmas)
        self.quantize_c_noise = quantize_c_noise
        self.num_idx = num_idx

    def sigma_to_idx(self, sigma: torch.Tensor) -> torch.Tensor:
        dists = sigma - self.sigmas[:, None]
        return dists.abs().argmin(dim=0).view(sigma.shape)

    def idx_to_sigma(self, idx: Union[torch.Tensor, int]) -> torch.Tensor:
        return self.sigmas[idx]

    def possibly_quantize_sigma(self, sigma: torch.Tensor) -> torch.Tensor:
        return self.idx_to_sigma(self.sigma_to_idx(sigma))

    def possibly_quantize_c_noise(self, c_noise: torch.Tensor) -> torch.Tensor:
        if self.quantize_c_noise:
            return self.sigma_to_idx(c_noise)
        else:
            return c_noise
