import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn.utils.rnn import pack_padded_sequence, pad_packed_sequence
from torch.nn import Conv1d, ReLU
from utils import common_util
import numpy as np

class ConnectionistTemporalClassification(nn.Module):
    def __init__(self, model_config):
        super(ConnectionistTemporalClassification, self).__init__()

        self.lstm = nn.LSTM(model_config.input_size,
                            model_config.lstm_dim,
                            num_layers=1, bidirectional=True, batch_first=True)

        self.model_config = model_config

        self.clf = nn.Linear(2 * model_config.lstm_dim, model_config.num_tags + 1)

        common_util.init_lstm_wt(self.lstm)
        common_util.init_linear_wt(self.hidden_layer)

    def forward(self, mfcc, length, **kwargs):
        lengths = length.view(-1).tolist()
        packed = pack_padded_sequence(mfcc, lengths, batch_first=True, enforce_sorted=False)
        output, hidden = self.lstm(packed)
        lstm_feats, _ = pad_packed_sequence(output, batch_first=True)  # h dim = B x t_k x n
        lstm_feats = lstm_feats.contiguous()
        logits = self.clf(lstm_feats)

        return logits

    def get_loss(self, logits, phone, length, **kwargs):
        b, t_k, d = list(logits.size())
        loss = 0
        for i in range(b):
            curr_len = length[i].cpu().data.numpy()
            curr_logit = logits[i].cpu().data.numpy()
            curr_phone = phone[i].cpu().data.numpy()
            loss += self.get_ctc_loss_single(curr_len, curr_logit, curr_phone)
        return loss

    '''
    For odd index return blank label 
    otherwise map the index to ground truth phone 
    index
    '''
    def get_phone_id(self, s, phone):
        if s % 2 == 1:
            return 0
        else:
            return phone[s // 2 - 1] + 1
    def has_same_label(self, s, phone):
        idx = s // 2
        return idx > 1 and phone[s // 2 - 1] == phone[s // 2 - 2]

    def get_ctc_loss_single(self, y, T, phone):
        num_phone = len(phone)
        S = 2*num_phone + 1
        alpha = np.zeros(T + 1, S + 1)
        t, s = 1, 1
        alpha[t, s] = y[t-1, self.get_phone_id(s, phone)]
        t, s = 1, 2
        alpha[t, s] = y[t - 1, self.get_phone_id(s, phone)]
        for t in range(2, T + 1):
            for s in range(1, S + 1):
                y_ = y[t - 1, self.get_phone_id(s, phone)]
                # blank or same labels
                if s % 2 == 1 or self.has_same_label(s, phone):
                    alpha[t, s] = (alpha[t-1, s] + alpha[t-1, s-1]) * y_
                else:
                    alpha[t, s] = (alpha[t-1, s] + alpha[t-1, s-1] + alpha[t-1, s-2]) * y_
        return alpha[T, S] + alpha[T, S - 1]

    def path_to_str(self, path):
        new_path = [path[0]] + [path[i] for i in range(1, len(path)) if path[i-1] != path[i]]
        return [p -1 for p in new_path if p > 0]

    def best_path_decode(self, logits, length):
        logits = logits.cpu().data.numpy()
        length = length.cpu().data.numpy()
        out_paths = []
        for y, curr_len in zip(logits, length):
            path = np.argmax(y[:curr_len, :], axis=1)
            out_paths.append(self.path_to_str(path))
        return out_paths

    def prefix_search_decode(self, logits, length):
        logprobs = logits.log_softmax(dim=2)
        logprobs = logprobs.cpu().data.numpy()
        length = length.cpu().data.numpy()

        out_paths = []
        for logprob, curr_len in zip(logprobs, length):
            beams = []
