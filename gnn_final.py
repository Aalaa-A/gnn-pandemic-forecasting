# -*- coding: utf-8 -*-
"""GNN-Final.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1QGNi_WZu3HN2rBobYhdDFWHJUmYmFL9B
"""

!nvidia-smi
!pip install dgl -f https://data.dgl.ai/wheels/torch-2.3/cu121/repo.html
!pip install haversine
!pip install optuna

"""importing all the necessary libraries"""

import pandas as pd
from haversine import haversine
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import dgl
import networkx as nx
from dgl.nn import GATConv
from sklearn.model_selection import train_test_split
import matplotlib.pyplot as plt
import optuna
import random

"""upload all data and metadata and combine
1. State Covid Cases (active cases only)
2. State Metadata (county level)
3. State Covid Data (recovered/hospitalised cases)
"""

cases_path = '/content/state_cases_data.csv'
cases_data = pd.read_csv(cases_path)
print(cases_data.head())
print(cases_data.columns)

metadata_path = '/content/drive/MyDrive/metadata.csv'
metadata = pd.read_csv(metadata_path)
print(metadata.head())
print(metadata.columns)

# metadata is by county so we have to combine it to state level datat
state_meta = metadata.groupby('state_name').agg({
    'population': 'sum',  # sum of all the county populations in a state
    'density': 'mean',    # calculate mean state population density
    'lat': 'mean',        # calculate mean state latitude
    'lng': 'mean'         # calculate mean state longitude
}).reset_index()

state_meta = state_meta.rename(columns={'state_name': 'State', 'population': 'Population', 'density': 'Population_Density', 'lat': 'Latitude', 'lng': 'Longitude'})
print(state_meta.head())

# combine metadata with state cases data
merged_data = pd.merge(cases_data, state_meta, how='inner', on='State')
print(merged_data.head())

#making all dates datetime format
date_columns = merged_data.columns[1:-4]
merged_data = merged_data.rename(columns=lambda x: pd.to_datetime(x).strftime('%Y-%m-%d') if x in date_columns else x)

# taking only 2020 data
columns_to_keep = [col for col in merged_data.columns if col.startswith('2020')]
non_date_columns = ['State', 'Population', 'Population_Density', 'Latitude', 'Longitude']
columns_to_keep = non_date_columns + columns_to_keep
df_2020 = merged_data[columns_to_keep]

#convert csv to long form
df_2020 = pd.melt(df_2020, id_vars=['State', 'Population', 'Population_Density', 'Latitude', 'Longitude'], var_name='Date', value_name='Confirmed_Cases')

print(df_2020)
df_2020.to_csv('df_2020.csv', index=False)

# finally combine that with the rest of the covid data on state and date (recovered/hospitalised)
other_covid_data_path = '/content/drive/MyDrive/state_covid_data_2020.csv'
other_covid_data = pd.read_csv(other_covid_data_path)
# print(other_covid_data.head())
# print(other_covid_data.columns)

final_covid_data = other_covid_data.drop(columns=['longitude', 'latitude', 'fips', 'confirmed'])
# final_covid_data.head()


final_covid_data = final_covid_data.rename(columns={'state': 'State', 'date_today': 'Date', 'active' : 'Active_Cases', 'hospitalization':'Hospitalised_Cases', 'new_cases': 'New_cases', 'deaths': 'Deaths', 'recovered': 'Recovered_Cases'})
final_data_complete = pd.merge(df_2020, final_covid_data, how='inner', on=['State', 'Date'])

print(final_data_complete.head())
final_data_complete.to_csv('final_data_complete.csv', index=False)

data = pd.read_csv('/content/final_data_complete.csv')
print(data.head())

"""Calculate state similarity using gravity law"""

def gravity_law(lat1, long1, pop1, lat2, long2, pop2, r=1e5, alpha=0.1, beta=0.1):
    """
    Calculates the gravity-law based distance between two points using longitude and latitude.
    r is a scaling factor, alpha and beta are weights for population.
    """
    distance = haversine((lat1, long1), (lat2, long2), 'km')
    weight = (np.exp(-distance / r)) / (abs((pop1 ** alpha) - (pop2 ** beta)) + 1e-5)
    return weight

similarity_dictionary = {}


#create list of all the states
state_list = list(data['State'].unique())

# print(state_list)

for state1 in state_list:
    # for every state create a dictionary for that state with other states
    similarity_dictionary[state1] = {}
    for state2 in state_list:
        # find latitude for the first state by making sure the state we're looking at is the same as the state in the loop, then get the corresponding latitude for that entry
        lat1 = data[data['State'] == state1]['Latitude'].values[0]
        long1 = data[data['State'] == state1]['Longitude'].values[0]
        pop1 = data[data['State'] == state1]['Population'].values[0]
        lat2 = data[data['State'] == state2]['Latitude'].values[0]
        long2 = data[data['State'] == state2]['Longitude'].values[0]
        pop2 = data[data['State'] == state2]['Population'].values[0]

        similarity_dictionary[state1][state2] = gravity_law(lat1, long1, pop1, lat2, long2, pop2)

similarity_dictionary

"""make the adjacency map"""

threshold = 17

#listing highest similarities first in the dict
for each_state1 in similarity_dictionary:
    similarity_dictionary[each_state1] = {key: value for key, value in sorted(similarity_dictionary[each_state1].items(), key=lambda item: item[1], reverse=True)}

# make the adjacency map, loops through each state in the similarity dict, if sim value is more than threshold and its in the top 3 states its added to the adjancy list
# if its less than the threshodl it adds the first state only
adjacency_map = {}
for each_state1 in similarity_dictionary:
    adjacency_map[each_state1] = []
    for i, each_state2 in enumerate(similarity_dictionary[each_state1]):
        if similarity_dictionary[each_state1][each_state2] > threshold:
            if i <= 3:
                adjacency_map[each_state1].append(each_state2)
            else:
                break
        else:
            if i <= 1:
                adjacency_map[each_state1].append(each_state2)
            else:
                break

rows = []
cols = []
for each_state1 in adjacency_map:
    for each_state2 in adjacency_map[each_state1]:
        rows.append(state_list.index(each_state1))
        cols.append(state_list.index(each_state2))

"""make graph"""

random.seed(42)
np.random.seed(42)

edge_rows = []
edge_cols = []
min_connections = 5

for state1 in similarity_dictionary:
    connections = 0
    extra_edges = []

    for state2 in similarity_dictionary[state1]:
        if state1 != state2 and similarity_dictionary[state1][state2] > threshold:
            edge_rows.append(state_list.index(state1))
            edge_cols.append(state_list.index(state2))
            connections += 1
        elif state1 != state2:  # Collect extra edges for potential addition
            extra_edges.append((state1, state2, similarity_dictionary[state1][state2]))

    # make sure each state has at least 5 connections
    if connections < min_connections:
        # most similarity first
        extra_edges = sorted(extra_edges, key=lambda x: (x[2], x[1]), reverse=True)

        # add more edges until atleast 5
        for extra_edge in extra_edges:
            if connections >= min_connections:
                break
            state2 = extra_edge[1]
            edge_rows.append(state_list.index(state1))
            edge_cols.append(state_list.index(state2))
            connections += 1

# create dgl graph
g = dgl.graph((edge_rows, edge_cols))

# visualise
nx_graph = g.to_networkx().to_undirected()
nx.draw(nx_graph, with_labels=True)

# with labels
state_abbreviations = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA",
    "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "Florida": "FL", "Georgia": "GA",
    "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA",
    "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO",
    "Montana": "MT", "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ",
    "New Mexico": "NM", "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH",
    "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT",
    "Virginia": "VA", "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
    "District of Columbia": "DC", "Puerto Rico": "PR"
}


state_labels = {i: state_abbreviations[state] for i, state in enumerate(state_list)}

layouts = [nx.spring_layout, nx.circular_layout, nx.kamada_kawai_layout]

for layout in layouts:
    plt.figure(figsize=(12, 8))
    pos = layout(nx_graph)
    nx.draw(nx_graph, pos, labels=state_labels, with_labels=True, node_color='skyblue', node_size=1500, font_size=15, font_weight='bold', edge_color='gray')
    plt.title(f"Graph using {layout.__name__}")
    plt.show()

"""make features"""

covid_data = data.sort_values(by=['State', 'Date'])
state_list = list(data['State'].unique())
dates_list = data['Date'].unique()


active_cases = []
confirmed_cases = []
new_cases = []
death_cases = []
recovered_cases=[]
static_feat = []


# features by state
for state in state_list:
    state_data = covid_data[covid_data['State'] == state]
    active_cases.append(state_data['Active_Cases'].values)
    confirmed_cases.append(state_data['Confirmed_Cases'].values)
    new_cases.append(state_data['New_cases'].values)
    death_cases.append(state_data['Deaths'].values)
    recovered_cases.append(state_data['Recovered_Cases'].values)
    static_feat.append(state_data[['Population', 'Population_Density', 'Longitude', 'Latitude']].iloc[0].values)



active_cases = np.array(active_cases)
confirmed_cases = np.array(confirmed_cases)
new_cases = np.array(new_cases)
death_cases = np.array(death_cases)
recovered_cases = np.array(recovered_cases)
static_feat = np.array(static_feat)


# pop of state - active cases - recovered = susceptible cases
susceptible_cases = np.expand_dims(static_feat[:, 0], axis=-1) - active_cases - recovered_cases


# day to day changes in active cases, recovered,and susceptivle
dInf = np.concatenate((np.zeros((active_cases.shape[0], 1), dtype=np.float32), np.diff(active_cases, axis=1)), axis=-1)
print("dInf shape:",dInf.shape)
# dDeath = np.concatenate((np.zeros((death_cases.shape[0], 1), dtype=np.float32), np.diff(death_cases, axis=1)), axis=-1)
# print ("dDeath")
# print(dDeath.shape)
dRec = np.concatenate((np.zeros((recovered_cases.shape[0], 1), dtype=np.float32), np.diff(recovered_cases, axis=1)), axis=-1)
# print ("dRec")
# print(dRecovered.shape)
dSus = np.concatenate((np.zeros((susceptible_cases.shape[0], 1), dtype=np.float32), np.diff(susceptible_cases, axis=1)), axis=-1)
# print ("dSus")
# print(dSus.shape)


#normalise features and store so we can unnormalise
def normalize_feature(feature):
    mean = np.mean(feature, axis=1, keepdims=True)
    std = np.std(feature, axis=1, keepdims=True)
    return (feature - mean) / (std + 1e-5), mean, std

normalised_dInf, mean_dInf, std_dInf = normalize_feature(dInf)
# normalised_dRec, mean_dRec, std_dRec = normalize_feature(dRec)
# normalised_dSus, mean_dSus, std_dSus = normalize_feature(dSus)

normaliser = {
    'dInf': {'mean': mean_dInf, 'std': std_dInf},
    # 'dRec': {'mean': mean_dRec, 'std': std_dRec},
    # 'dSus': {'mean': mean_dSus, 'std': std_dSus}
}

# dynamic_feat = np.concatenate((normalised_dInf[..., np.newaxis], normalised_dRec[..., np.newaxis], normalised_dSus[..., np.newaxis]),axis=-1)

dynamic_feat = normalised_dInf[..., np.newaxis]

print("dynamic feat shape:",dynamic_feat.shape)



def prep_data(data, sum_I, history_window=5, pred_window=15, slide_step=5):
    n_loc = data.shape[0]
    timestep = data.shape[1]
    n_feat = data.shape[2]

    x = []
    y_I = []
    last_I = []
    concat_I = []

    for i in range(0, timestep, slide_step):
        if i + history_window + pred_window - 1 >= timestep or i + history_window >= timestep:
            break
        x.append(data[:, i:i + history_window, :].reshape((n_loc, history_window * n_feat)))

        concat_I.append(data[:, i + history_window - 1, 0])
        last_I.append(sum_I[:, i + history_window - 1])

        y_I.append(data[:, i + history_window:i + history_window + pred_window, 0])

    print("x shape before transpose:", np.array(x).shape)
    print("y_I shape before transpose:", np.array(y_I).shape)
    print("concat_I shape before transpose:", np.array(concat_I).shape)
    print("last_I shape before transpose:", np.array(last_I).shape)

    x = np.array(x, dtype=np.float32).transpose((1, 0, 2))
    last_I = np.array(last_I, dtype=np.float32).transpose((1, 0))
    concat_I = np.array(concat_I, dtype=np.float32).transpose((1, 0))
    y_I = np.array(y_I, dtype=np.float32).transpose((1, 0, 2))

    return x, last_I, concat_I, y_I

history_window, pred_window, slide_step = 5, 10, 1
valid_window, test_window = 25, 25

train_feat = dynamic_feat[:, :-valid_window-test_window, :]
val_feat = dynamic_feat[:, -valid_window-test_window:-test_window, :]
test_feat = dynamic_feat[:, -test_window:, :]

train_x, train_I, train_cI, train_yI = prep_data(train_feat, active_cases[:, :-valid_window-test_window], history_window, pred_window, slide_step)
val_x, val_I, val_cI, val_yI = prep_data(val_feat, active_cases[:, -valid_window-test_window:-test_window], history_window, pred_window, slide_step)
test_x, test_I, test_cI, test_yI = prep_data(test_feat, active_cases[:, -test_window:], history_window, pred_window, slide_step)


print("Number of windows in training set:", train_x.shape[1])
print("Number of windows in validation set:", val_x.shape[1])
print("Number of windows in test set:", test_x.shape[1])

print(f"Total time steps: {dynamic_feat.shape[1]}")
print(f"Train data time steps: {train_feat.shape[1]}")
print(f"Val data time steps: {val_feat.shape[1]}")
print(f"Test data time steps: {test_feat.shape[1]}")
print(f'train_feat shape: {train_feat.shape}')
print(f"train x shape: {train_x.shape}")
print(f"train yI shape: {train_yI.shape}")
print(f"train cI shape: {train_cI.shape}")
print(f"val_feat shape: {val_feat.shape}")
print(f"val x shape: {val_x.shape}")
print(f"val yI shape: {val_yI.shape}")
print(f"val cI shape: {val_cI.shape}")
print(f"test_feat shape: {test_feat.shape}")
print(f"test x shape: {test_x.shape}")
print(f"test yI shape: {test_yI.shape}")

"""make model"""

def initialise_weights(m):
    if isinstance(m, nn.Linear):
        nn.init.xavier_uniform_(m.weight)
        nn.init.zeros_(m.bias)
    elif isinstance(m, nn.GRUCell):
        for name, param in m.named_parameters():
            if 'weight_ih' in name:
                nn.init.xavier_uniform_(param.data)
            elif 'weight_hh' in name:
                nn.init.orthogonal_(param.data)
            elif 'bias' in name:
                param.data.fill_(0)

class GAT(nn.Module):
    def __init__(self, g, in_dim, out_dim):
        super(GAT, self).__init__()
        self.g = g
        self.fc = nn.Linear(in_dim, out_dim)
        self.attn_fc = nn.Linear(2 * out_dim, 1)
        self.reset_parameters()

    def reset_parameters(self):
        gain = nn.init.calculate_gain('relu')
        nn.init.xavier_normal_(self.fc.weight, gain=gain)
        nn.init.xavier_normal_(self.attn_fc.weight, gain=gain)

    def edge_attention(self, edges):
        z2 = torch.cat([edges.src['z'], edges.dst['z']], dim=1)
        a = self.attn_fc(z2)
        return {'e': F.leaky_relu(a)}

    def message_func(self, edges):
        return {'z': edges.src['z'], 'e': edges.data['e']}

    def reduce_func(self, nodes):
        alpha = F.softmax(nodes.mailbox['e'], dim=1)
        h = torch.sum(alpha * nodes.mailbox['z'], dim=1)
        return {'h': h}

    def forward(self, h):
        z = self.fc(h)
        self.g.ndata['z'] = z
        self.g.apply_edges(self.edge_attention)
        self.g.update_all(self.message_func, self.reduce_func)
        return self.g.ndata.pop('h')

class MHGAT(nn.Module):
    def __init__(self, g, in_dim, out_dim, num_heads, merge='cat'):
        super(MHGAT, self).__init__()
        self.heads = nn.ModuleList()
        for i in range(num_heads):
            self.heads.append(GAT(g, in_dim, out_dim))
        self.merge = merge

    def forward(self, h):
        head_outs = [attn_head(h) for attn_head in self.heads]
        if self.merge == 'cat':
            return torch.cat(head_outs, dim=1)
        else:
            return torch.mean(torch.stack(head_outs))

class GNN(nn.Module):
    def __init__(self, g, in_dim, hidden_dim1, hidden_dim2, gru_dim, num_heads, pred_window, device):
        super(GNN, self).__init__()
        self.g = g

        self.layer1 = MHGAT(self.g, in_dim, hidden_dim1, num_heads)
        self.layer2 = MHGAT(self.g, hidden_dim1 * num_heads, hidden_dim2, 1)

        self.pred_window = pred_window
        self.gru = nn.GRUCell(hidden_dim2, gru_dim)

        self.nn_res_I = nn.Linear(gru_dim + 1, pred_window)

        self.nn_res_sir = nn.Linear(gru_dim + 1, 1)

        self.hidden_dim2 = hidden_dim2
        self.gru_dim = gru_dim
        self.device = device

    def forward(self, dynamic, cI, N, I, h=None):
        num_loc, timestep, n_feat = dynamic.size()
        N = N.squeeze()

        if h is None:
            h = torch.zeros(1, self.gru_dim).to(self.device)
            gain = nn.init.calculate_gain('relu')
            nn.init.xavier_normal_(h, gain=gain)

        new_I = []
        phy_I = []
        self.alpha_list = []
        self.alpha_scaled = []

        for each_step in range(timestep):
            cur_h = self.layer1(dynamic[:, each_step, :])

            if torch.isnan(cur_h).any():
                print("NaN detected after layer1 at timestep:", each_step)

            cur_h = F.elu(cur_h)
            cur_h = self.layer2(cur_h)

            if torch.isnan(cur_h).any():
                print("NaN detected after layer2 at timestep:", each_step)

            cur_h = F.elu(cur_h)

            cur_h = torch.max(cur_h, 0)[0].reshape(1, self.hidden_dim2)

            h = self.gru(cur_h, h)

            if torch.isnan(h).any():
                print("NaN detected after GRU at timestep:", each_step)

            hc = torch.cat((h, cI[each_step].reshape(1,1)), dim=1)

            pred_I = self.nn_res_I(hc)
            new_I.append(pred_I)

            pred_res = self.nn_res_sir(hc)
            alpha = pred_res[:, 0]

            self.alpha_list.append(alpha)
            alpha = torch.sigmoid(alpha)
            self.alpha_scaled.append(alpha)

            cur_phy_I = []
            for i in range(self.pred_window):
                last_I = I[each_step] if i == 0 else last_I + dI.detach()
                last_S = N - last_I

                dI = alpha * last_I * (last_S/N)
                cur_phy_I.append(dI)

            cur_phy_I = torch.stack(cur_phy_I).to(self.device).permute(1, 0)
            phy_I.append(cur_phy_I)

        new_I = torch.stack(new_I).to(self.device).permute(1, 0, 2)
        phy_I = torch.stack(phy_I).to(self.device).permute(1, 0, 2)

        self.alpha_list = torch.stack(self.alpha_list).squeeze()
        self.alpha_scaled = torch.stack(self.alpha_scaled).squeeze()

        return new_I, phy_I, h

in_dim = history_window
hidden_dim1 = 32
hidden_dim2 = 32
gru_dim = 32
num_heads = 1
device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

g = g.to(device)
model = GNN(g, in_dim, hidden_dim1, hidden_dim2, gru_dim, num_heads, pred_window, device).to(device)
model.apply(initialise_weights)
optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
criterion = nn.MSELoss()

train_x = torch.tensor(train_x).to(device)
train_I = torch.tensor(train_I).to(device)
# train_R = torch.tensor(train_R).to(device)
train_cI = torch.tensor(train_cI).to(device)
# train_cR = torch.tensor(train_cR).to(device)
train_yI = torch.tensor(train_yI).to(device)
# train_yR = torch.tensor(train_yR).to(device)

val_x = torch.tensor(val_x).to(device)
val_I = torch.tensor(val_I).to(device)
# val_R = torch.tensor(val_R).to(device)
val_cI = torch.tensor(val_cI).to(device)
# val_cR = torch.tensor(val_cR).to(device)
val_yI = torch.tensor(val_yI).to(device)
# val_yR = torch.tensor(val_yR).to(device)

test_x = torch.tensor(test_x).to(device)
test_I = torch.tensor(test_I).to(device)
# test_R = torch.tensor(test_R).to(device)
test_cI = torch.tensor(test_cI).to(device)
# test_cR = torch.tensor(test_cR).to(device)
test_yI = torch.tensor(test_yI).to(device)
# test_yR = torch.tensor(test_yR).to(device)

dInf_mean = torch.tensor(mean_dInf, dtype=torch.float32).to(device).reshape((mean_dInf.shape[0], 1, 1))
dInf_std = torch.tensor(std_dInf, dtype=torch.float32).to(device).reshape((std_dInf.shape[0], 1, 1))
# dR_mean = torch.tensor(mean_dR, dtype=torch.float32).to(device).reshape((mean_dR.shape[0], 1, 1))
# dR_std = torch.tensor(std_dR, dtype=torch.float32).to(device).reshape((std_dR.shape[0], 1, 1))

N = torch.tensor(static_feat[:, 0], dtype=torch.float32).to(device).unsqueeze(-1)

"""train the model for the state of california"""

train_x = train_x.float()
train_cI = train_cI.float()
N = N.float()
train_I = train_I.float()
val_x = val_x.float()
val_cI = val_cI.float()
val_I = val_I.float()

all_loss = []
all_rmse = []
all_mae = []
val_rmse_list = []
val_mae_list = []
file_name = 'best_stan_model1.pth'
min_loss = 1e10

state_name = 'California'
current_state = state_list.index(state_name)

for epoch in range(50):
    model.train()
    optimizer.zero_grad()


    active_pred, phy_active, _ = model(train_x, train_cI[current_state], N[current_state], train_I[current_state])
    phy_active = (phy_active - dInf_mean[current_state]) / dInf_std[current_state]

    loss = criterion(active_pred.squeeze(), train_yI[current_state]) + \
           0.1 * criterion(phy_active.squeeze(), train_yI[current_state])


    loss.backward()
    optimizer.step()
    all_loss.append(loss.item())


    train_rmse = np.sqrt(loss.item())
    all_rmse.append(train_rmse)


    train_mae = torch.mean(torch.abs(active_pred.squeeze() - train_yI[current_state]))
    all_mae.append(train_mae.item())

    model.eval()
    with torch.no_grad():
        _, val_phy_active, _ = model(val_x, val_cI[current_state], N[current_state], val_I[current_state])

        val_phy_active = (val_phy_active - dInf_mean[current_state]) / dInf_std[current_state]
        val_loss = criterion(val_phy_active.squeeze(), val_yI[current_state])

        val_rmse = np.sqrt(val_loss.item())
        val_rmse_list.append(val_rmse)


        val_mae = torch.mean(torch.abs(val_phy_active.squeeze() - val_yI[current_state]))
        val_mae_list.append(val_mae.item())

        if val_loss < min_loss:
            state = {
                'state': model.state_dict(),
                'optimizer': optimizer.state_dict(),
            }
            torch.save(state, file_name)
            min_loss = val_loss
            print('-----Save best model-----')

    print(f'Epoch {epoch}, Loss {all_loss[-1]:.2f}, RMSE {train_rmse:.2f}, MAE {train_mae.item():.2f}, Val loss {val_loss.item():.2f}, Val RMSE {val_rmse:.2f}, Val MAE {val_mae.item():.2f}')