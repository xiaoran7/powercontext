import { createDatabaseClient, createSiteContext, errorResponse, BackendError } from './_qoder/backend.mjs';
import { application } from './routes.mjs';

// 公开站点：业务路由不强制登录，访问者以自生成 learner_id 隔离各自的复习画像。
const handle = application({ db: createDatabaseClient(), BackendError });

Deno.serve(async request => {
  let context;
  try {
    context = createSiteContext(request);
    return await handle(context);
  } catch (error) {
    return errorResponse(error, context?.requestId);
  }
});
