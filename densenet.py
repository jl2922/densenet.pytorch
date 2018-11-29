import torch

import torch.nn as nn
import torch.optim as optim

import torch.nn.functional as F
from torch.autograd import Variable

import torchvision.datasets as dset
import torchvision.transforms as transforms
from torch.utils.data import DataLoader

import torchvision.models as models

import sys
import math

class Bottleneck(nn.Module):
    def __init__(self, nChannels, growthRate):
        super(Bottleneck, self).__init__()
        interChannels = 4 * growthRate
        self.bn1 = nn.BatchNorm2d(nChannels)
        self.conv1 = nn.Conv2d(nChannels, interChannels, kernel_size=1,
                               bias=False)
        self.bn2 = nn.BatchNorm2d(interChannels)
        self.conv2 = nn.Conv2d(interChannels, growthRate, kernel_size=3,
                               padding=1, bias=False)

    def forward(self, x):
        out = self.conv1(F.relu(self.bn1(x)))
        out = self.conv2(F.relu(self.bn2(out)))
        out = torch.cat((x, out), 1)
        return out

class SingleLayer(nn.Module):
    def __init__(self, nChannels, growthRate):
        super(SingleLayer, self).__init__()
        self.bn1 = nn.BatchNorm2d(nChannels)
        self.conv1 = nn.Conv2d(nChannels, growthRate, kernel_size=3,
                               padding=1, bias=False)

    def forward(self, x):
        out = self.conv1(F.relu(self.bn1(x)))
        out = torch.cat((x, out), 1)
        return out

class Transition(nn.Module):
    def __init__(self, nChannels, nOutChannels):
        super(Transition, self).__init__()
        self.bn1 = nn.BatchNorm2d(nChannels)
        self.conv1 = nn.Conv2d(nChannels, nOutChannels, kernel_size=1,
                               bias=False)
        self.dropout = nn.Dropout(p=0.2)

    def forward(self, x):
        out = self.conv1(F.relu(self.bn1(x)))
        out = self.dropout(out)
        out = F.avg_pool2d(out, 2)
        return out


class DenseNet(nn.Module):
    def __init__(self, growthRate, depth, reduction, nClasses, bottleneck):
        super(DenseNet, self).__init__()

        nDenseBlocks = (depth-4) // 3
        if bottleneck:
            nDenseBlocks //= 2

        nChannels = 2*growthRate
        self.conv1 = nn.Conv2d(3, nChannels, kernel_size=3, padding=1,
                               bias=False)
        self.dense1 = self._make_dense(nChannels, growthRate, nDenseBlocks, bottleneck)
        nChannels += nDenseBlocks*growthRate
        nOutChannels = int(math.floor(nChannels*reduction))
        self.trans1 = Transition(nChannels, nOutChannels)

        nChannels = nOutChannels
        self.dense2 = self._make_dense(nChannels, growthRate, nDenseBlocks, bottleneck)
        nChannels += nDenseBlocks*growthRate
        nOutChannels = int(math.floor(nChannels*reduction))
        self.trans2 = Transition(nChannels, nOutChannels)

        nChannels = nOutChannels
        self.dense3 = self._make_dense(nChannels, growthRate, nDenseBlocks, bottleneck)
        nChannels += nDenseBlocks*growthRate

        self.bn1 = nn.BatchNorm2d(nChannels)

        self.hiddenDim = 1024
        self.nHiddenLayers = 4
        self.hiddenInitFc1 = nn.Linear(1, self.hiddenDim)
        self.hiddenInitFc2= nn.Linear(1, self.hiddenDim)
        self.lstm = nn.LSTM(nChannels, self.hiddenDim, self.nHiddenLayers, dropout=0.1)
        self.hiddenTotal = self.hiddenDim * self.nHiddenLayers
        self.repeats = 10

        self.bn2 = nn.BatchNorm1d(self.hiddenDim)
        self.dropout = nn.Dropout(p=0.2)
        # self.fc1 = nn.Linear(self.hiddenDim, self.hiddenDim * 2)
        self.fc2 = nn.Linear(self.hiddenDim, nClasses)

        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
                m.weight.data.normal_(0, math.sqrt(2. / n))
            elif isinstance(m, nn.BatchNorm2d):
                m.weight.data.normal_(1, 1e-3)
                m.bias.data.normal_(0, 1e-3)
            elif isinstance(m, nn.Linear):
                m.bias.data.normal_(0, 1e-3)

    def _make_dense(self, nChannels, growthRate, nDenseBlocks, bottleneck):
        layers = []
        for i in range(int(nDenseBlocks)):
            if bottleneck:
                layers.append(Bottleneck(nChannels, growthRate))
            else:
                layers.append(SingleLayer(nChannels, growthRate))
            nChannels += growthRate
        return nn.Sequential(*layers)

    def forward(self, x):
        out = self.conv1(x)
        out = self.trans1(self.dense1(out))
        out = self.trans2(self.dense2(out))
        out = self.dense3(out)
        out = F.avg_pool2d(F.relu(self.bn1(out)), 8)
        # print(out.size())
        out = out.view(1, out.size()[0], -1)
        # print(out.size())
        # out = torch.squeeze(out.view(out.size()[0], out.size()[1], -1))
        # out = out.unsqueeze(0)
        hidden = (self.hiddenInitFc1(self.hiddenStarter), self.hiddenInitFc2(self.hiddenStarter))

        for _ in range(self.repeats):
            # lstmOut, self.hidden = self.lstm(out, self.hidden)
            lstmOut, hidden = self.lstm(out, hidden)

        out = torch.squeeze(lstmOut)
        out = self.bn2(out)
        out = self.dropout(out)
        out = F.log_softmax(self.fc2(out))
        return out

    def init_hidden_starter(self, batchSize):
        return torch.zeros(self.nHiddenLayers, batchSize, 1)
        # return (torch.zeros(self.nHiddenLayers, batchSize, self.hiddenDim),
        #        torch.zeros(self.nHiddenLayers, batchSize, self.hiddenDim))
