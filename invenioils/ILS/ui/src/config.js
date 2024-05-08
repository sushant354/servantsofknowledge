export const config = {
  APP: {
    LOGO_SRC: process.env.PUBLIC_URL + '/images/logo-invenio-ils.svg',
    ENABLE_LOCAL_ACCOUNT_LOGIN: true,
    ENABLE_OAUTH_LOGIN: false,
    OAUTH_PROVIDERS: null,
    EMAILS_PREFILL: {
      subjectPrefix: null,
      footer: null,
    },
    ENVIRONMENTS: [],
  },
};
