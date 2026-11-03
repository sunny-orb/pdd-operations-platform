import { z } from "zod";
export const supplementSchema = z.object({
  shop_id: z.string(),
  shop_name: z.string(),
  date: z.string(),
  source: z.string(),
  source_url: z.string().nullable(),
  source_record_id: z.string().nullable(),
  spend: z.string(),
  revenue: z.string(),
  status: z.enum(["supplement", "warehouse", "outside_range"]),
});
