# ILS

Welcome to your InvenioILS instance.

## Getting started

Run the `./setup.sh` script in order to setup your new InvenioILS instance.

Run the backend:

```console
pipenv run invenio run --cert docker/backend/test.crt --key docker/backend/test.key
```

**Note** The server will listen to HTTPS requests and it uses a self-signed SSL certificate.
If this is not the desired behaviour, you can run the server with HTTP only by:

1.  Changing REACT_APP_INVENIO_UI_URL and REACT_APP_INVENIO_REST_ENDPOINTS_BASE_URL variables in `ui/.env` file to run on `http` instead of `https`.
2.  Running the server without specifying the certificate: `FLASK_ENV=development pipenv run invenio run`

To start the UI part, navigate to the `ui/` folder and run:

```console
npm i
npm start
```

The above commands first builds the application docker image and afterwards
starts the application and related services (database, Elasticsearch, Redis
and RabbitMQ). The build and boot process will take some time to complete,
especially the first time as docker images have to be downloaded during the
process.

Once running, visit https://127.0.0.1:3000 in your browser.

## Overview

Following is an overview of the generated files and folders:

| Name                      | Description                                                                                                      |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `docker`                  | Example configuration for NGINX and uWSGI. Consists Dockerfiles for building backend and frontend docker images. |
| `templates`               | Folder for your Jinja templates.                                                                                 |
| `ui`                      | Web assets (CSS, JavaScript, LESS, JSX templates) used in the Webpack build.                                     |
| `vocabularies`            | Folder for vocabularies (mappings, schemas, etc).                                                                |
| `docker-compose.full.yml` | Example of a full infrastructure stack.                                                                          |
| `docker-compose.yml`      | Backend services needed for local development.                                                                   |
| `docker-services.yml`     | Common services for the Docker Compose files.                                                                    |
| `invenio.cfg`             | The Invenio application configuration.                                                                           |
| `Pipfile`                 | Python requirements installed via [pipenv](https://pipenv.pypa.io)                                               |
| `Pipfile.lock`            | Locked requirements (generated on first install).                                                                |

## Documentation

To learn how to configure, customize, deploy and much more, visit
the [InvenioILS Documentation](https://invenioils.docs.cern.ch/).
