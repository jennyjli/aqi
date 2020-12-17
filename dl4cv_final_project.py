# -*- coding: utf-8 -*-
"""DL4CV final project.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1Zl9gWfnmo50o4DltJ_qfZENJ-ndqsEpw
"""

import wandb

wandb.init(entity="jennyjli", project="aqi")

def AirQualityCategory(aqi):
  if aqi <= 50:
    return 'Good'
  elif aqi <= 100:
    return 'Moderate'
  elif aqi <= 150:
    return 'Unhealthy for Sensitive Groups'
  elif aqi <= 200:
    return 'Unhealthy'
  elif aqi <= 300:
    return 'Very unhealthy'
  else:
    return 'Hazardous'

import pandas as pd
locations = ['WA','CA', 'UT', 'MT', 'NM', 'SD','IL', 'FL','NC', 'NY']
aqi = []
for loc in locations:
    county = pd.read_csv('aqi/'+loc+'.csv')
    county['State'] = loc
    aqi.append(county)
aqi = pd.concat(aqi)

import glob
from skimage.io import imread
from skimage import transform
from torch.utils.data import Dataset, DataLoader
from torchvision import transforms
import numpy as np

class ToTensor(object):
    def __call__(self, sample):
        sat, cli, label = sample['sat'], sample['cli'], sample['label']
        sat = sat.transpose((2, 0, 1))
        cli = cli.transpose((2, 0, 1))
        return {'sat': torch.from_numpy(sat).float(), 'cli': torch.from_numpy(cli).float(), 'label': torch.tensor([label]).float()}

class Normalize(object):
    def __call__(self, sample):
        normalize_in = transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
        sat, cli, label = sample['sat'], sample['cli'], sample['label']
        #sat = normalize_in(torch.from_numpy(sat).float())
        cli = normalize_in(cli)
        return {'sat': sat, 'cli': cli, 'label':label}

class Rescale(object):
    def __call__(self, sample):
        sat, cli, label = sample['sat'], sample['cli'], sample['label']
        #print("before sat shape",sat.shape)
        #print("before cli shape",cli.shape)
        sat = transform.resize(sat, (224, 224))
        cli = transform.resize(cli, (224, 224))
        #print("sat shape",sat.shape)
        #print("cli shape",cli.shape)
        return {'sat': sat, 'cli': cli, 'label':label}

class AQIDataset(Dataset):
    def __init__(self, aqis, sats, clis, transforms=None):
        self.aqis = aqis
        self.transform = transforms
        self.to_tensor = ToTensor()
        self.sats = sats
        self.clis = clis

    def __len__(self):
        return len(self.aqis)

    def __getitem__(self, idx):
        sample = {'sat': self.sats[idx], 'cli': self.clis[idx], 'label':self.aqis[idx]}

        if self.transform:
            sample = self.transform(sample)
        sample = self.to_tensor(sample)
        return sample

sat_ext = [ 'sat.B'+str(i)+'.tif' for i in range(1,12)]
cli_ext = ['cli.cloud_fraction.tif', 'cli.cloud_top_pressure.tif', 'cli.cloud_base_pressure.tif']
count = 0
aqi_data, sat_data, cli_data = [],[],[]
for zip_name in glob.glob('./ee-data/'+'*-sat.zip'):
    fn = zip_name.split('/')[-1]
    date = fn[:10].split('-')
    mon, day = date[1], date[2]
    state = fn[10:12]
    no_ext = zip_name[:-7]
    tmp_df = aqi[(aqi['Date']==mon+'/'+day+'/2020') & (aqi['State']==state)]
    if tmp_df.shape[0] == 1:
        pm25 = tmp_df.iloc[0]['PM2.5 AQI Value']
        sat, cli = [], []
        for sat_e in sat_ext:
            sat.append(imread(no_ext+sat_e))
        sat_img = np.stack(sat, axis=2)
        for cli_e in cli_ext:
            cli.append(imread(no_ext+cli_e))
        cli_img = np.stack(cli, axis=2)
        aqi_data.append(pm25)
        sat_data.append(sat_img)
        cli_data.append(cli_img)
dataset = AQIDataset(aqi_data, sat_data, cli_data, transforms=transforms.Compose([Rescale(), Normalize()]))
print(len(dataset))

import torch
lengths = [int(len(dataset)*0.9), len(dataset)-int(len(dataset)*0.9)]
trainset, testset = torch.utils.data.random_split(dataset, lengths)
lengths = [int(len(trainset)*0.85), len(trainset)-int(len(trainset)*0.85)]
trainset, valset = torch.utils.data.random_split(trainset, lengths)
print(len(trainset))
print(len(valset))
print(len(testset))

import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np
import torchvision
from torchvision import datasets, models, transforms
import matplotlib.pyplot as plt
import os
import copy
import torch.nn.functional as F

class AqiModel(nn.Module):
    def __init__(self):
        super(AqiModel, self).__init__()
        
        sat_modules = list(models.resnet18(pretrained=True).children())[:-1]
        self.modelA = nn.Sequential(nn.Conv2d(11, 3, 3, 1, 1), *sat_modules)

        cli_modules = list(models.resnet18(pretrained=True).children())[:-1]
        self.modelB = nn.Sequential(*cli_modules)
        
        self.fc = nn.Linear(1024, 1)
        
    def forward(self, sat, cli):
        a = self.modelA(sat)
        b = self.modelB(cli)
        x = torch.cat((a.view(a.size(0), -1), b.view(b.size(0), -1)), dim=1)
        x = self.fc(x)
        x = F.sigmoid(x)
        return x

# batch size
batch_size = 32

# a method to train a model
def train_model(model, dataloaders, criterion, optimizer, device, num_epochs=5):
    # Keep track of the best model based on validation performance
    best_model_wts = copy.deepcopy(model.state_dict())
    best_loss = 1e10

    # repeat for num_epochs
    for epoch in range(num_epochs):
        # print progress
        print('Epoch {}/{}'.format(epoch, num_epochs - 1))
        print('-' * 10)

        # For each epoch go through training and validation data
        for phase in ['train', 'val']:
            if phase == 'train':
                model.train()  # Set model to training mode
            else:
                model.eval()   # Set model to evaluate mode

            # keep track of loss and corrects in the epoch
            running_loss = 0.0
            # Iterate over data.
            for sample in dataloaders[phase]:
                # use torch with the device that does the computation
                sat = sample['sat'].to(device)
                cli = sample['cli'].to(device)
                labels = sample['label'].to(device)
                # clear the gradient for this step
                optimizer.zero_grad()
                # enable gradients if in training mode
                with torch.set_grad_enabled(phase == 'train'):
                    # forward pass
                    outputs = model(sat, cli)
                    # compute loss
                    loss = criterion(outputs, labels)
                    
                    if phase == 'train':
                        # if training, backprop
                        loss.backward()
                        optimizer.step()
                # update loss and corrects

                running_loss += loss.item() * sat.size(0)
            # compute loss and accuracy or the epoch
            epoch_loss = running_loss / len(dataloaders[phase].dataset)
            # print data
            print('{} Loss: {:.4f}'.format(phase, epoch_loss))
            if phase == 'train':
                wandb.log({phase+'/Epoch Loss': epoch_loss})
            # update the best model if we get a better accuracy
            if phase == 'val' and epoch_loss < best_loss:
                best_loss = epoch_loss
                best_model_wts = copy.deepcopy(model.state_dict())

    # load the best model to return
    model.load_state_dict(best_model_wts)
    return model

model = AqiModel()

# Create training and validation datasets
image_datasets = {'train':trainset, 'val':valset, 'test':testset}
# Create training and validation dataloaders
dataloaders_dict = {x: torch.utils.data.DataLoader(image_datasets[x], batch_size=batch_size, shuffle=True, num_workers=4) for x in ['train', 'val']}

# Use GPU if available
device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
model = model.to(device)

# initialize optimizer
optimizer = optim.Adam(model.parameters(), lr=0.0001)

# Use cross entropy loss
criterion = nn.MSELoss()

# Train and evaluate
print("Training started...")
model = train_model(model, dataloaders_dict, criterion, optimizer, device, num_epochs=1000)
torch.save(model.state_dict(), "aqi-model-1000ep.pth")
