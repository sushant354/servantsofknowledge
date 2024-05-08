FROM node:14 AS react-build

ENV WORKING_DIR=/code/ui
RUN mkdir -p ${WORKING_DIR}
COPY ./ui ${WORKING_DIR}
WORKDIR ${WORKING_DIR}

# build React application
RUN npm install
RUN REACT_APP_INVENIO_UI_URL="https://127.0.0.1" \
    REACT_APP_INVENIO_REST_ENDPOINTS_BASE_URL="https://127.0.0.1/api" \
    REACT_APP_ENV_NAME="dev" \
    npm run build

FROM nginx:1.18

COPY docker/frontend/nginx.conf /etc/nginx/nginx.conf
COPY docker/frontend/conf.d/* /etc/nginx/conf.d/
COPY docker/frontend/test.key /etc/ssl/private/test.key
COPY docker/frontend/test.crt /etc/ssl/certs/test.crt

ENV INVENIO_ASSETS=/usr/share/nginx/html/invenio-assets
ENV REACT_ASSETS=/usr/share/nginx/html/react-assets

RUN mkdir -p ${INVENIO_ASSETS}
RUN mkdir -p ${REACT_ASSETS}

COPY --from=react-build /code/ui/build ${REACT_ASSETS}
CMD ["nginx", "-g", "daemon off;"]
