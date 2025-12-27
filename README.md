# NewCo

ML and Logistics

# Recipe Engine (Scaling + Calibration)

This package implements a safe, explainable recipe scaling engine and a lightweight “self-learning” calibration step.

## 1) Baseline scaling equation

We store a baseline recipe for 10 people:

- q10_g: grams for 10 people
- b: scaling exponent (how fast an ingredient grows)
- c_g: minimum floor in grams

For N people:

q(N) = q10_g \* (N/10)^b + c_g

This prevents flavor ingredients (salt/spices) from scaling linearly.

## 2) Protein selection (optional)

If a recipe includes multiple protein rows (e.g., chicken/fish/beef) but only one is used per batch:

- include only the selected protein
- set the other protein rows to 0

## 3) Calibration (self-learning) using batch logs

We keep b fixed (safe), and learn a per-ingredient scale factor s_i from real CookBatch logs:

pred_scalable = q10_g \* (N/10)^b
ratio = actual_g / pred_scalable

We compute s_i as a weighted mean of ratio, where newer batches count more:

weight = exp(-age_days / tau_days)

Then predictions become:

q_new(N) = s_i \* pred_scalable + c_g

## 4) Why this is useful

- Works immediately with small/no data.
- Gets better as the kitchen logs real batches.
- Stores clean logs (suggested/final/actual) so you can train a full ML model later if needed.
