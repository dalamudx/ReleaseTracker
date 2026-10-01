import {afterEach, describe, expect, it} from "vitest"
import {apiClient} from "@/api/client"
import {authorizeAdminOIDCBinding, getAdminOIDCBinding, unbindAdminOIDC} from "@/api/oidc"

const adapter = apiClient.defaults.adapter
afterEach(()=>{
    apiClient.defaults.adapter=adapter
    document.cookie="releasetracker-csrf=; max-age=0; path=/"
})

describe("OIDC administration via browser cookies",()=>{
    it("shares CSRF headers, credentials, and never reads a legacy access token",async()=>{
        document.cookie="releasetracker-csrf=csrf-test; path=/"
        localStorage.setItem("token", "must-not-be-sent")
        let writes=0
        apiClient.defaults.adapter=async config=>{
            expect(config.withCredentials).toBe(true)
            expect(config.headers.Authorization).toBeUndefined()
            if(config.method!=="get") {
                writes++
                expect(config.headers["X-CSRF-Token"]).toBe("csrf-test")
            }
            return {config, status:200, statusText:"OK", headers:{}, data:{bound:false, authorization_url:"https://idp.example", message:"ok"}}
        }
        try {
            await getAdminOIDCBinding()
            await authorizeAdminOIDCBinding(1,"password")
            await unbindAdminOIDC("password")
            expect(writes).toBe(2)
        } finally {localStorage.removeItem("token")}
    })
})
