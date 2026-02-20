import { useTranslation } from "react-i18next"
import { Button } from "@/components/ui/button"
import { Loader2 } from "lucide-react"
import { motion } from "framer-motion"
import type { OIDCProvider } from "@/api/oidc"

interface OIDCLoginButtonProps {
    provider: OIDCProvider
    onLogin: (providerSlug: string) => void
    isLoading?: boolean
}

export function OIDCLoginButton({ provider, onLogin, isLoading }: OIDCLoginButtonProps) {
    const { t } = useTranslation()

    return (
        <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.25 }}
        >
            <Button
                type="button"
                variant="outline"
                className="w-full h-11 justify-center gap-2 bg-background/50 border-input hover:bg-accent/50 transition-all duration-150"
                onClick={() => onLogin(provider.slug)}
                disabled={isLoading}
            >
                {isLoading ? (
                    <>
                        <Loader2 className="h-4 w-4 animate-spin flex-shrink-0" />
                        <span className="text-sm">{t('auth.oidc.connecting', { name: provider.name })}</span>
                    </>
                ) : (
                    <>
                        <span className="text-sm">{t('auth.oidc.loginWith_prefix')}</span>
                        {provider.icon_url ? (
                            <img
                                src={provider.icon_url}
                                alt={provider.name}
                                className="h-5 w-5 object-contain flex-shrink-0"
                            />
                        ) : (
                            <div className="h-5 w-5 rounded-full bg-primary/20 flex items-center justify-center text-xs font-bold flex-shrink-0">
                                {provider.name.charAt(0).toUpperCase()}
                            </div>
                        )}
                        <span className="text-sm font-medium">{provider.name}</span>
                        <span className="text-sm">{t('auth.oidc.loginWith_suffix')}</span>
                    </>
                )}
            </Button>
        </motion.div>
    )
}
