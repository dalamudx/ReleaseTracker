import "@/i18n/version-policy"
import { useTranslation } from "react-i18next"
import type { UseFormReturn } from "react-hook-form"
import { FormControl, FormField, FormItem, FormLabel } from "@/components/ui/form"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import type { ExecutorFormValues } from "./executorSheetHelpers"

export function AutomaticVersionPolicyField({ form }: { form: UseFormReturn<ExecutorFormValues> }) {
    const { t } = useTranslation("versionPolicy")
    return <FormField control={form.control} name="auto_update_policy" render={({ field }) => (
        <FormItem>
            <FormLabel>{t("label")}</FormLabel>
            <Select value={field.value ?? "all"} onValueChange={field.onChange}>
                <FormControl><SelectTrigger><SelectValue /></SelectTrigger></FormControl>
                <SelectContent>{(["all", "minor", "patch"] as const).map(value => (
                    <SelectItem key={value} value={value}>{t(value)}</SelectItem>
                ))}</SelectContent>
            </Select>
            <p className="text-sm text-muted-foreground">{t("hint")}</p>
        </FormItem>
    )} />
}
