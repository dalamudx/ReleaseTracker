import { expect, test, type Page } from "@playwright/test"

const published = "2026-10-01T12:00:00Z"
const trackerNames = Array.from({length:15},(_,i)=>`mobile-nginx-${i+1}`)
const source={id:1,source_key:"image",source_type:"container",source_config:{registry:"docker.io",image:"library/nginx"},enabled:true,source_rank:0,release_channels:[{release_channel_key:"stable",name:"stable",enabled:true,type:"release",last_version:"1.30.0",deploy_alias:"stable"}]}
const trackers=trackerNames.map((name,i)=>({id:i+1,name,enabled:true,primary_changelog_source_key:"image",sources:[{...source,id:i+1}],channels:[],interval:360,fetch_limit:10,fetch_timeout:15,status:{last_check:published,last_version:"1.30.0",error:null,source_count:1,enabled_source_count:1,source_types:["container"]}}))
const releases=Array.from({length:6},(_,i)=>({tracker_release_history_id:i+1,identity_key:`digest:${i}`,tracker_name:`mobile-release-${i+1}`,tracker_type:"container",primary_source_type:"container",primary_source:{source_key:"image",source_type:"container",source_release_history_id:i+1},name:"NGINX release",tag_name:`1.30.${i}-alpine-long-platform-release`,version:`1.30.${i}`,published_at:published,created_at:published,projected_at:published,url:"https://example.invalid/nginx",body:"# Release\n\nMobile release notes content.",prerelease:false,digest:"sha256:"+"a".repeat(64),channel_name:"stable",channel_keys:["stable"]}))
const executors=Array.from({length:8},(_,i)=>({id:i+1,name:`mobile-executor-${i+1}`,runtime_type:"docker",runtime_connection_id:1,tracker_name:trackers[0].name,tracker_source_id:1,enabled:true,update_mode:"manual",image_selection_mode:"replace_tag_on_current_image",image_reference_mode:"tag",target_ref:{mode:"container",container_name:"isolated-nginx"},status:null}))
const credentials=Array.from({length:8},(_,i)=>({id:i+1,name:`mobile-credential-${i+1}`,type:"github",description:"Isolated mobile layout example",created_at:published,runtime_connections_count:0}))
const runtimes=Array.from({length:8},(_,i)=>({id:i+1,name:`mobile-runtime-${i+1}`,type:"docker",enabled:true,config:{socket:"unix:///var/run/docker.sock"},secrets:{},endpoint:"unix:///var/run/docker.sock",description:"Isolated fixture only"}))
const tasks=Array.from({length:8},(_,i)=>({id:i+1,kind:"fetch",state:"succeeded",target_label:`mobile-task-${i+1}`,attempts:1,max_retries:3,due_at:1790000000,created_at:1790000000,updated_at:1790000000,result:{},target:{tracker_name:trackerNames[0]},error_code:null}))
const notifiers=Array.from({length:8},(_,i)=>({id:`mobile-notifier-${i+1}`,name:`mobile-notifier-${i+1}`,type:"webhook",enabled:true,url:"https://example.invalid/hook",events:[],config:{},channel_names:[],tracker_names:[],template_id:null}))

async function setup(page:Page,width:number,height:number,language="zh"){
 await page.setViewportSize({width,height})
 await page.addInitScript(language=>{document.cookie="releasetracker-csrf=fixture; path=/";localStorage.setItem("language",language)},language)
 await page.route(url=>url.pathname.startsWith("/api/"),route=>{
  const url=new URL(route.request().url());const path=url.pathname
  const send=(data:unknown)=>route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(data)})
  if(path==="/api/auth/me")return send({id:1,username:"mobile-fixture",is_admin:true})
  if(path==="/api/stats")return send({total_trackers:15,total_releases:45,recent_releases:6,latest_update:published,channel_stats:{stable:45},release_type_stats:{release:45},daily_stats:Array.from({length:7},(_,i)=>({date:`2026-09-${24+i}`,count:3,channels:{stable:i+1}}))})
  if(path==="/api/releases/latest")return send(releases)
  if(path==="/api/releases")return send({items:releases,total:6})
  if(path==="/api/trackers")return send({items:trackers,total:15})
  if(path.startsWith("/api/trackers/")){
   const name=decodeURIComponent(path.split("/")[3]);const item=trackers.find(t=>t.name===name)??trackers[0]
   if(path.endsWith("/current"))return send({tracker:{name,primary_changelog_source_key:"image",sources:[]},status:item.status,latest_release:null,matrix:{columns:[],rows:[]},projected_at:published})
   if(path.includes("/releases/history"))return send({tracker:name,items:[],total:0})
   return send(item)
  }
  if(path==="/api/executors")return send({items:executors,total:8})
  if(path==="/api/credentials")return send({items:credentials,total:8})
  if(path==="/api/runtime-connections")return send({items:runtimes,total:8})
  if(path==="/api/tasks")return send(tasks)
  if(path==="/api/notifiers")return send({items:notifiers,total:8})
  if(path==="/api/settings/security-keys")return send({jwt_secret:{configured:true,fingerprint:"fixture",active_sessions:1},encryption_key:{configured:true,fingerprint:"fixture",undecryptable_count:0,inventory:{credentials_token:0,credentials_secrets:0,oauth_provider_client_secret:0,runtime_connection_secrets:0}}})
  if(path==="/api/webhooks/repositories")return send([{id:"mobile-repo-hook",tracker_name:trackerNames[0],tracker_source_id:1,source_key:"image",provider:"github",enabled:true,release_published:true,workflow_success:true,endpoint_url:"https://example.invalid/hook",secret_configured:true}])
  if(path==="/api/settings"||path.includes("/templates")||path.includes("/oauth/providers"))return send([])
  return send({items:[],total:0})
 })
}

for(const size of [{width:390,height:640},{width:320,height:568},{width:768,height:1024},{width:844,height:390},{width:1280,height:800}]){
 test(`mobile dashboard and tracker data ${size.width}`,async({page})=>{
  await setup(page,size.width,size.height);const errors:string[]=[];page.on("pageerror",e=>errors.push(e.message))
  await page.goto("/")
  await expect(page.locator(".recharts-surface")).toBeVisible()
  const trend=page.getByText("发布趋势",{exact:true}).locator("xpath=ancestor::*[@data-slot='card']")
  const recent=page.getByText("最近发布",{exact:true}).locator("xpath=ancestor::*[@data-slot='card']")
  await expect(trend.getByText("28",{exact:true})).toBeVisible()
  const trendBounds=(await trend.boundingBox())!;const chartBounds=(await trend.locator(".recharts-surface").boundingBox())!
  expect(chartBounds.y+chartBounds.height).toBeLessThanOrEqual(trendBounds.y+trendBounds.height)
  expect(trendBounds.width).toBeLessThanOrEqual(size.width)
  console.log("dashboard geometry",trendBounds,await recent.boundingBox())
  await trend.scrollIntoViewIfNeeded()
  await page.screenshot({path:`/tmp/rt-mobile-dashboard-${size.width}.png`})
  const svg=trend.locator(".recharts-surface")
  expect((await svg.boundingBox())!.height).toBeGreaterThanOrEqual(200)
  if(size.width>=1280)expect((await recent.boundingBox())!.x).toBeGreaterThanOrEqual((await trend.boundingBox())!.x+(await trend.boundingBox())!.width)
  else expect((await recent.boundingBox())!.y).toBeGreaterThanOrEqual((await trend.boundingBox())!.y+(await trend.boundingBox())!.height)
  const last=recent.getByText("mobile-release-6",{exact:true});await last.scrollIntoViewIfNeeded();await expect(last).toBeVisible()
  await page.screenshot({path:`/tmp/rt-mobile-recent-${size.width}.png`})
  expect(await recent.evaluate(n=>n.scrollWidth<=n.clientWidth)).toBe(true)
  const notes=recent.locator("li").last().getByRole("button")
  if((await recent.boundingBox())!.width<576){expect((await notes.boundingBox())!.width).toBeGreaterThanOrEqual(44);expect((await notes.boundingBox())!.height).toBeGreaterThanOrEqual(44)}
  await notes.click()
  await expect(page.getByRole("dialog")).toContainText("Mobile release notes content.")
  await page.keyboard.press("Escape")
  await expect(page.getByRole("dialog")).toHaveCount(0)
  await page.goto("/trackers")
  await expect(page.getByText("mobile-nginx-1",{exact:true}).first()).toBeVisible()
  const pane=page.locator("#tracker-list-pane");console.log("tracker geometry",await pane.boundingBox())
  expect(await pane.locator("table").evaluate(table=>table.scrollWidth<=table.parentElement!.clientWidth)).toBe(true)
  for(const button of await pane.locator("tbody tr").first().getByRole("button").all()){const b=(await button.boundingBox())!;expect(b.x+b.width).toBeLessThanOrEqual(size.width)}
  await page.screenshot({path:`/tmp/rt-mobile-trackers-${size.width}.png`})
  expect((await pane.boundingBox())!.height).toBeGreaterThan(150)
  const item=pane.getByText("mobile-nginx-15",{exact:true});await item.scrollIntoViewIfNeeded();await expect(item).toBeVisible();await item.click()
  const detail=page.locator("#tracker-detail-pane");await expect(detail).toBeVisible()
  if(size.width<1280){
    await expect(pane).not.toBeVisible()
    await page.getByRole("button",{name:"返回追踪器列表",exact:true}).click()
    await expect(pane).toBeVisible();await expect(detail).not.toBeVisible()
  }else{await expect(pane).toBeVisible();await expect(page.getByRole("separator",{name:"调整追踪器列表与详情面板宽度"})).toBeVisible()}
  const check=pane.getByRole("button",{name:"检查",exact:true}).last()
  await check.scrollIntoViewIfNeeded()
  expect((await check.boundingBox())!.x+(await check.boundingBox())!.width).toBeLessThanOrEqual(size.width)
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  expect(errors).toEqual([])
 })
}

for(const language of ["zh","en"]) for(const width of [320,390]) test(`audit remaining mobile pages ${language} ${width} with nonempty data`,async({page})=>{
 await setup(page,width,640,language);const errors:string[]=[];page.on("pageerror",e=>errors.push(e.message));const reports=[]
 for(const [route,text] of [["/executors","mobile-executor-1"],["/tasks","mobile-task-1"],["/history","mobile-release-1"],["/credentials","mobile-credential-1"],["/runtime-connections","mobile-runtime-1"],["/webhooks","mobile-notifier-1"],["/settings",""]]){
  await page.goto(route)
  if(text)await expect(page.getByText(text,{exact:true}).first()).toBeVisible()
  await expect(page.locator("main")).toBeVisible()
  if(route==="/webhooks"||route==="/settings") {
    const tabs=page.getByRole("tablist").first();const bounds=(await tabs.boundingBox())!
    for(const tab of await tabs.getByRole("tab").all()){const b=(await tab.boundingBox())!;expect(b.y+b.height).toBeLessThanOrEqual(bounds.y+bounds.height+1)}
  }
  if(route==="/history"||route==="/webhooks"){
    const buttons=page.locator("tbody tr").first().getByRole("button")
    for(const button of await buttons.all()){const b=(await button.boundingBox())!;expect(b.x+b.width).toBeLessThanOrEqual(width)}
  }
  const report=await page.locator("main").evaluate(main=>({mainHeight:main.clientHeight,scrollHeight:main.scrollHeight,clipped:main.scrollWidth>main.clientWidth,tables:Array.from(main.querySelectorAll("table")).map(table=>({height:table.getBoundingClientRect().height,containerHeight:table.parentElement!.getBoundingClientRect().height}))}))
  reports.push({route,...report});await page.screenshot({path:`/tmp/rt-mobile-audit-${route.slice(1)}-${language}-${width}.png`})
  if(route==="/webhooks"){
    await page.getByRole("tab",{name:language==="zh" ? /仓库 Webhook/ : /Repository Webhooks/}).click()
    await expect(page.getByText(trackerNames[0],{exact:true}).first()).toBeVisible()
    for(const button of await page.locator("tbody tr").first().getByRole("button").all()){const b=(await button.boundingBox())!;expect(b.x+b.width).toBeLessThanOrEqual(width)}
  }
 }
 console.log("mobile audit",JSON.stringify(reports));expect(errors).toEqual([]);expect(reports.filter(r=>r.clipped)).toEqual([])
})
