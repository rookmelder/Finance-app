# Finding: Code changes need a Docker rebuild
> The image copies src/, so run docker compose up -d --build after every change or the old code keeps running.

Back to [Findings](findings.md). Learned 2026-11-23 while working on [Deployment](../systems/system-deployment.md).

## What happened
After editing templates and CSS the site looked unchanged: the container still ran the image built before the edit.

## What works
`docker compose up -d --build`. The database lives in the named volume `finance-data`, so rebuilding keeps the data.

## What didn't
Restarting the container without `--build`.
