from django.contrib import admin
from .models import CookBatch, CookBatchItem


class CookBatchItemInline(admin.TabularInline):
    model = CookBatchItem
    extra = 0
    fields = (
        "ingredient",
        "group",
        "pred_g",
        "final_g",
        "was_clamped",
        "actual_g",
        "notes",
    )
    readonly_fields = (
        "ingredient",
        "group",
        "pred_g",
        "final_g",
        "was_clamped",
    )
    ordering = ("id",)


@admin.register(CookBatch)
class CookBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "recipe", "n_people", "protein_type", "status", "created_at")
    list_filter = ("status", "recipe")
    search_fields = ("recipe__name", "protein_type", "notes")
    ordering = ("-created_at",)
    inlines = [CookBatchItemInline]


@admin.register(CookBatchItem)
class CookBatchItemAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "batch",
        "ingredient",
        "group",
        "pred_g",
        "final_g",
        "was_clamped",
        "actual_g",
    )
    list_filter = ("group", "was_clamped")
    search_fields = ("ingredient", "batch__recipe__name")
    ordering = ("-batch__created_at", "id")
