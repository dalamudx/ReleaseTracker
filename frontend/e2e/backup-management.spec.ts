import { expect, test } from "@playwright/test"
for(const language of ["zh","en"])for(const width of [320,1280]){
 test(`backup deletion and safety ${language} ${width}`,async({page})=>{
  await page.setViewportSize({width,height:900});await page.addInitScript(language=>{document.cookie="releasetracker-csrf=isolated-fixture; path=/";localStorage.setItem("language",language)},language)
  const first="releasetracker-1791000000000000000-deadbeef.zip", second="releasetracker-1791000000000000001-deadbeef.zip"
  let items=[{name:first,size:1048576,created_at:1791000000},{name:second,size:1048576,created_at:1791000001}]
  let deletes=0;let fail=true;const errors:string[]=[];page.on("pageerror",error=>errors.push(error.message))
  await page.route(url=>url.pathname.startsWith("/api/"),async route=>{
   const req=route.request();const path=new URL(req.url()).pathname
   const send=(data:unknown,status=200)=>route.fulfill({status,contentType:"application/json",body:JSON.stringify(data)})
   if(path==="/api/auth/me")return send({id:1,username:"isolated-admin",is_admin:true})
   if(path==="/api/settings/security-keys")return send({jwt_secret:{configured:true,fingerprint:"fixture",active_sessions:1},encryption_key:{configured:true,fingerprint:"fixture",undecryptable_count:0,inventory:{credentials_token:0,credentials_secrets:0,oauth_provider_client_secret:0,runtime_connection_secrets:0}}})
   if(path==="/api/backups")return send({items,retention:7,interval_hours:24,daily_retention:14,weekly_retention:4,total_size:items.length*1048576,running:false,minimum_local_archives:1})
   if(path===`/api/backups/${first}` && req.method()==="DELETE"){
    expect(req.postDataJSON()).toEqual({confirm_name:first});deletes++
    if(fail){fail=false;return send({detail:"backup_in_use"},409)}
    items=items.filter(item=>item.name!==first);return send({deleted:first})
   }
   if(path==="/api/settings"||path.includes("/oauth/providers")||path==="/api/tasks")return send([])
   return send({items:[],total:0})
  })
  await page.goto("/settings")
  await page.getByRole("tab",{name:language==="zh"?"备份":"Backups",exact:true}).click()
  await expect(page.getByText(first,{exact:true})).toBeVisible()
  const deleteName=language==="zh"?"删除":"Delete"
  await page.getByRole("button",{name:`${deleteName} ${first}`,exact:true}).click()
  const dialog=page.getByRole("alertdialog");await expect(dialog).toContainText(first);expect(deletes).toBe(0)
  await dialog.getByRole("button",{name:language==="zh"?"取消":"Cancel",exact:true}).click();expect(deletes).toBe(0)
  await page.getByRole("button",{name:`${deleteName} ${first}`,exact:true}).click()
  const confirm=dialog.getByRole("button",{name:language==="zh"?"确认删除备份":"Confirm backup deletion",exact:true})
  await confirm.click();await expect(dialog.getByRole("alert")).toContainText(language==="zh"?"正在下载":"being downloaded")
  expect(deletes).toBe(1);await expect(confirm).toBeEnabled()
  await confirm.click();await expect(dialog).not.toBeVisible();await expect(page.getByText(first,{exact:true})).toHaveCount(0)
  await expect(page.getByRole("button",{name:`${deleteName} ${second}`,exact:true})).toBeDisabled()
  expect(deletes).toBe(2);expect(errors).toEqual([])
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  await page.screenshot({path:`/tmp/rt-backup-management-${language}-${width}.png`})
 })
}
