#!/bin/bash
cd ..
docker build -t gcr.io/halfr-cloud-stechec/sadm-base . -f kubernetes/Dockerfile.sadm-base && docker push gcr.io/halfr-cloud-stechec/sadm-base
docker build -t gcr.io/halfr-cloud-stechec/sadm . -f kubernetes/Dockerfile && docker push gcr.io/halfr-cloud-stechec/sadm
