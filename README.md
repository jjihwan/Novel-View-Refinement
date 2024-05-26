# Novel View Refinement


## Setting up

#### PyTorch 2.0

```shell
conda activate sv3d python==3.10.14
pip3 install -r requirements.txt
```

#### Install `sgm`
```shell
git clone https://github.com/Stability-AI/generative-models.git
cd generative-models
pip3 install .
```

#### Install `sdata` for training
```shell
pip3 install -e git+https://github.com/Stability-AI/datapipelines.git@main#egg=sdata
```


## Training
```shell
sh scripts/sv3d_finetune.sh
```


## Inference
```shell
sh scripts/inference.sh
```