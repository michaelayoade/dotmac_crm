SHELL := /usr/bin/env bash

CURRENT_TAG := sha-$(shell git rev-parse --short HEAD)
TAG ?= $(CURRENT_TAG)

.PHONY: help
help:
	@printf '%s\n' 'DotMac CRM deploy targets'
	@printf '%s\n' ''
	@printf '%s\n' 'Usage:'
	@printf '%s\n' '  make deploy-status'
	@printf '%s\n' '  make deploy-check-image TAG=sha-a1ec14e'
	@printf '%s\n' '  make deploy TAG=sha-a1ec14e'
	@printf '%s\n' '  make deploy-no-backup TAG=sha-a1ec14e'
	@printf '%s\n' '  make deploy-current'
	@printf '%s\n' '  make deploy-current-no-backup'
	@printf '%s\n' ''
	@printf '%s\n' 'Scenarios:'
	@printf '%s\n' '  deploy                  GHCR image deploy with pre-migration backup.'
	@printf '%s\n' '  deploy-no-backup        GHCR image deploy for UI/config-only changes.'
	@printf '%s\n' '  deploy-current          Deploy sha-$$(git rev-parse --short HEAD) with backup.'
	@printf '%s\n' '  deploy-current-no-backup Deploy sha-$$(git rev-parse --short HEAD) without backup.'

.PHONY: deploy-status
deploy-status:
	bash scripts/deploy.sh --status

.PHONY: deploy-check-image
deploy-check-image:
	docker manifest inspect ghcr.io/michaelayoade/dotmac_crm:$(TAG) >/dev/null
	@printf 'Image exists: ghcr.io/michaelayoade/dotmac_crm:%s\n' '$(TAG)'

.PHONY: deploy
deploy: deploy-check-image
	bash scripts/deploy.sh $(TAG)

.PHONY: deploy-no-backup
deploy-no-backup: deploy-check-image
	SKIP_BACKUP=1 bash scripts/deploy.sh $(TAG)

.PHONY: deploy-current
deploy-current:
	$(MAKE) deploy TAG=$(CURRENT_TAG)

.PHONY: deploy-current-no-backup
deploy-current-no-backup:
	$(MAKE) deploy-no-backup TAG=$(CURRENT_TAG)
