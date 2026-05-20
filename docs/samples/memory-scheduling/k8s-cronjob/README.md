# Kubernetes CronJob scheduling

Three manifests:

- [`configmap.yaml`](configmap.yaml) — the two preset YAMLs mounted at
  `/etc/chunkshop/memory/`.
- [`realtime-cronjob.yaml`](realtime-cronjob.yaml) — runs every minute.
- [`consolidate-cronjob.yaml`](consolidate-cronjob.yaml) — runs nightly.

Plus a Secret you create yourself for the DSN:

```bash
kubectl create secret generic chunkshop-pg \
  --from-literal=dsn="postgresql://app:secret@db.svc:5432/agent_memory"
```

Then apply:

```bash
kubectl apply -f configmap.yaml
kubectl apply -f realtime-cronjob.yaml
kubectl apply -f consolidate-cronjob.yaml
```

`concurrencyPolicy: Forbid` on both is important — it prevents a slow
consolidate run from being shadowed by the next scheduled fire. The
realtime cell is idempotent, but you still don't want two writers
racing on the watermark.

`successfulJobsHistoryLimit` is tuned low (1) so kubelet doesn't
accumulate completed Pods; `failedJobsHistoryLimit: 3` keeps a small
window for forensics.

If your cluster runs both Python and Rust crates, point each CronJob
at the right image — `chunkshop:0.4.4` (Python) or `chunkshop-rs:0.4.5`
(Rust, once tagged). The preset YAMLs work with both.
