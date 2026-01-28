import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { useAuth } from "@/providers/AuthProvider"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Rocket, ArrowRight, Loader2 } from "lucide-react"
import { motion } from "framer-motion"

export function LoginPage() {
    const navigate = useNavigate()
    const { login, isLoading } = useAuth()
    const [formData, setFormData] = useState({
        username: "",
        password: ""
    })
    const [isSubmitting, setIsSubmitting] = useState(false)

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault()
        setIsSubmitting(true)
        try {
            await login(formData)
            navigate("/")
        } catch (error) {
            // Error handled in login function
        } finally {
            setIsSubmitting(false)
        }
    }

    const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const { name, value } = e.target
        setFormData(prev => ({
            ...prev,
            [name]: value
        }))
    }

    return (
        <div className="relative min-h-screen flex items-center justify-center overflow-hidden bg-background">
            {/* Dynamic Background matching theme */}
            <div className="absolute inset-0 w-full h-full overflow-hidden">
                <div className="absolute top-0 -left-4 w-72 h-72 bg-primary/20 rounded-full mix-blend-multiply filter blur-3xl opacity-30 animate-blob dark:mix-blend-color-dodge"></div>
                <div className="absolute top-0 -right-4 w-72 h-72 bg-secondary/20 rounded-full mix-blend-multiply filter blur-3xl opacity-30 animate-blob animation-delay-2000 dark:mix-blend-color-dodge"></div>
                <div className="absolute -bottom-8 left-20 w-72 h-72 bg-accent/20 rounded-full mix-blend-multiply filter blur-3xl opacity-30 animate-blob animation-delay-4000 dark:mix-blend-color-dodge"></div>
            </div>

            <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5 }}
                className="relative z-10 w-full max-w-md px-4"
            >
                <div className="bg-card/50 backdrop-blur-xl border border-border/50 rounded-2xl shadow-xl overflow-hidden">
                    <div className="p-8 sm:p-10">
                        <div className="flex flex-col items-center mb-8 space-y-2">
                            <motion.div
                                initial={{ scale: 0 }}
                                animate={{ scale: 1 }}
                                transition={{ type: "spring", stiffness: 260, damping: 20, delay: 0.1 }}
                                className="p-3 bg-primary/10 rounded-xl shadow-sm"
                            >
                                <Rocket className="h-8 w-8 text-primary" />
                            </motion.div>
                            <h1 className="text-3xl font-bold tracking-tight text-foreground">Release Tracker</h1>
                            <p className="text-sm text-muted-foreground">管理和追踪您的软件发布版本</p>
                        </div>

                        <form onSubmit={handleSubmit} className="space-y-6">
                            <div className="space-y-2">
                                <Label htmlFor="username">用户名</Label>
                                <Input
                                    id="username"
                                    name="username"
                                    type="text"
                                    placeholder="输入用户名"
                                    value={formData.username}
                                    onChange={handleInputChange}
                                    required
                                    className="bg-background/50 border-input focus:ring-primary/20"
                                />
                            </div>
                            <div className="space-y-2">
                                <div className="flex items-center justify-between">
                                    <Label htmlFor="password">密码</Label>
                                </div>
                                <Input
                                    id="password"
                                    name="password"
                                    type="password"
                                    placeholder="输入密码"
                                    value={formData.password}
                                    onChange={handleInputChange}
                                    required
                                    className="bg-background/50 border-input focus:ring-primary/20"
                                />
                            </div>

                            <div className="flex justify-center">
                                <Button
                                    type="submit"
                                    className="w-1/2 h-11 transition-all duration-200 hover:scale-[1.02] active:scale-[0.98]"
                                    disabled={isSubmitting || isLoading}
                                >
                                    {isSubmitting ? (
                                        <>
                                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                            登录中...
                                        </>
                                    ) : (
                                        <>
                                            登录
                                            <ArrowRight className="ml-2 h-4 w-4" />
                                        </>
                                    )}
                                </Button>
                            </div>
                        </form>
                    </div>
                </div>
            </motion.div>

            {/* Custom Animation Styles */}
            <style>{`
                @keyframes blob {
                    0% { transform: translate(0px, 0px) scale(1); }
                    33% { transform: translate(30px, -50px) scale(1.1); }
                    66% { transform: translate(-20px, 20px) scale(0.9); }
                    100% { transform: translate(0px, 0px) scale(1); }
                }
                .animate-blob {
                    animation: blob 7s infinite;
                }
                .animation-delay-2000 {
                    animation-delay: 2s;
                }
                .animation-delay-4000 {
                    animation-delay: 4s;
                }
            `}</style>
        </div>
    )
}
