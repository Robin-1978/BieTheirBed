const app = require("./app.json").expo;

const projectId = (process.env.KNOA_EXPO_PROJECT_ID ?? "").trim();

module.exports = {
  ...app,
  extra: {
    ...(app.extra ?? {}),
    eas: {
      projectId,
    },
  },
};
