import { expect, test } from "@playwright/test"
for(const language of ["zh","en"])for(const width of [320,1280]){
 test(`online restore preflight confirmation and review ${language} ${width}`,async({page})=>{
  await page.setViewportSize({width,height:900});await page.addInitScript(language=>{document.cookie="releasetracker-csrf=fixture; path=/";localStorage.setItem("language",language)},language)
  const name="releasetracker-1791000000000000000-deadbeef.zip";let submitted=0,reviewed=false,statusReads=0
  const id="a".repeat(32),token="b".repeat(43),hash="c".repeat(64)
  const errors:string[]=[];page.on("pageerror",error=>{errors.push(error.message);console.error("PAGE_ERROR",error.message)})
  await page.route(url=>url.pathname.startsWith("/api/"),async route=>{
   const req=route.request();const path=new URL(req.url()).pathname
   const send=(data:unknown,status=200)=>route.fulfill({status,contentType:"application/json",body:JSON.stringify(data)})
   if(path==="/api/auth/me")return send({id:1,username:"admin",is_admin:true})
   if(path==="/api/backups")return send({items:[{name,size:1000,created_at:1791000000}],retention:7,interval_hours:0,running:false,online_restore_available:true,restore_review_required:submitted>0&&!reviewed})
   if(path.endsWith("/restore-plan"))return send({id,name,fingerprint:hash,expires_at:9999999999,created_at:1791000000,app_version:"1.1.14",mutation_performed:false})
   if(path.endsWith("/restore")&&req.method()==="POST"){
    expect(req.postDataJSON()).toEqual({plan_id:id,fingerprint:hash,confirm_name:name,data_loss_confirmed:true});submitted++;return send({id,token},202)
   }
   if(path.startsWith("/api/backups/restore-status/")){
    expect(req.headers()["x-restore-token"]).toBe(token);statusReads++
    return send({id,state:"succeeded",phase:"finished",error_code:null,rolled_back:false,review_required:true})
   }
   if(path==="/api/backups/restore-review"){expect(req.postDataJSON()).toEqual({reviewed:true});reviewed=true;return send({review_required:false})}
   if(path==="/api/settings"||path.includes("/oauth/providers")||path==="/api/tasks")return send([])
   if(path.includes("security-keys"))return send({jwt_secret:{configured:true,fingerprint:"fixture",active_sessions:1},encryption_key:{configured:true,fingerprint:"fixture",undecryptable_count:0,inventory:{}}})
   return send({items:[],total:0})
  })
  await page.goto("/settings");await page.getByRole("tab",{name:language==="zh"?"备份":"Backups",exact:true}).click()
  await page.getByRole("button",{name:`${language==="zh"?"在线恢复":"Restore online"} ${name}`,exact:true}).click()
  const dialog=page.getByRole("alertdialog");const confirm=dialog.getByRole("button",{name:language==="zh"?"进入维护并恢复":"Enter maintenance and restore",exact:true})
  await expect(confirm).toBeDisabled()
  await dialog.getByLabel(language==="zh"?"输入完整备份文件名以确认":"Type the complete backup filename to confirm").fill(name)
  await expect(confirm).toBeDisabled();await dialog.getByRole("checkbox").check();await expect(confirm).toBeEnabled()
  await expect(dialog).toContainText(hash);expect(await dialog.evaluate(element=>element.scrollWidth<=element.clientWidth)).toBe(true)
  await page.screenshot({path:`/tmp/rt-online-restore-review-${language}-${width}.png`});expect(submitted).toBe(0)
  await confirm.click();await expect(dialog).not.toBeVisible()
  await expect(page.getByRole("region",{name:language==="zh"?"在线恢复进度":"Online restore progress",exact:true}).getByRole("status")).toContainText(language==="zh"?"恢复完成":"Restored and services reloaded")
  expect(submitted).toBe(1);expect(statusReads).toBeGreaterThan(0)
  await page.reload();await page.getByRole("tab",{name:language==="zh"?"备份":"Backups",exact:true}).click()
  await expect(page.getByRole("region",{name:language==="zh"?"在线恢复进度":"Online restore progress",exact:true}).getByRole("status")).toContainText(language==="zh"?"恢复完成":"Restored and services reloaded")
  await page.getByRole("button",{name:language==="zh"?"重新登录":"Sign in again",exact:true}).click()
  await expect(page).toHaveURL(/\/login$/)
  await page.goto("/settings");await page.getByRole("tab",{name:language==="zh"?"备份":"Backups",exact:true}).click()
  await page.getByRole("button",{name:language==="zh"?"复核恢复状态":"Review restored state",exact:true}).click()
  expect(reviewed).toBe(false)
  await page.getByRole("alertdialog").getByRole("button",{name:language==="zh"?"已核对现场，恢复新操作":"Remote state reviewed; resume new operations",exact:true}).click()
  await expect(page.getByRole("button",{name:language==="zh"?"复核恢复状态":"Review restored state",exact:true})).toHaveCount(0)
  expect(reviewed).toBe(true);expect(errors).toEqual([])
 })
}
