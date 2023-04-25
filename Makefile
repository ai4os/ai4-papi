VIRTUALENV ?= virtualenv
NOMAD_ADDR ?= https://localhost:4646
NOMAD_CACERT ?= nomad-ca.pem
NOMAD_CLIENT_CERT ?= cli.pem
NOMAD_CLIENT_KEY ?= cli-key.pem 

ifndef VERBOSE
.SILENT:
endif

.PHONY: help
help:
	@echo 'Makefile to run and test the AI4EOSC Platform API                      '
	@echo '                                                                       '
	@echo 'Usage:                                                                 '
	@echo '   make setup      generate a virtualenv and install the API inside it '
	@echo '   make run        execute the API service from the virtualenv         '
	@echo '   make develop    generate a virtualenv and install the API inside it '
	@echo '	                  in develop mode (i.e. pip install -e )              '
	@echo '   make run-dev    execute the API service from the virtualenv         '
	@echo '                   in development mode (i.e. --reload)                 '
	@echo '   make regenerate regenerate the virtualenv                           '
	@echo '   make clean      cleanup the existing virtualenv                     '
	@echo '                                                                       '
	@echo 'Environment variables:                                                 '
	@echo '	VIRTUALENV: path for the virtualenv (defaults to "virtualenv")        '

.PHONY: setup
setup: $(VIRTUALENV) 
	. $(VIRTUALENV)/bin/activate; cd $(VIRTUALENV); pip uninstall -y ai4papi
	. $(VIRTUALENV)/bin/activate; pip install .

.PHONY: develop
develop: $(VIRTUALENV) 
	. $(VIRTUALENV)/bin/activate; cd $(VIRTUALENV); pip uninstall -y ai4papi
	. $(VIRTUALENV)/bin/activate; pip install -e .

#.PHONY: develop
#develop: requirements.txt virtualenv install
#
regenerate: | clean $(VIRTUALENV)

$(VIRTUALENV): requirements.txt
	@echo 'D> Creating $(VIRTUALENV)...'
	virtualenv --python=python3 $(VIRTUALENV)
	. $(VIRTUALENV)/bin/activate; pip install -r requirements.txt

.PHONY: clean 
clean:
	@echo 'D> Deleting $(VIRTUALENV)...'
	rm -rf $(VIRTUALENV)

.PHONY: run 
run: setup $(VIRTUALENV)
	@echo 'D> Running DEEPaaS inside $(VIRTUALENV)'
	. $(VIRTUALENV)/bin/activate; uvicorn ai4papi.main:app

.PHONY: run-dev
run-dev: develop $(VIRTUALENV)
	@echo 'D> Running DEEPaaS inside $(VIRTUALENV) in development mode'
	. $(VIRTUALENV)/bin/activate; uvicorn ai4papi.main:app --reload
