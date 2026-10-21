package com.parentstv.live.data

import android.content.Context

enum class MenuKeyAction {
    Favorite,
    Lines,
    Aspect,
    Off;

    companion object {
        fun from(value: String?): MenuKeyAction =
            values().firstOrNull { it.name == value } ?: Favorite
    }
}

enum class BackKeyAction {
    HideControls,
    BackToList;

    companion object {
        fun from(value: String?): BackKeyAction =
            values().firstOrNull { it.name == value } ?: HideControls
    }
}

class TvPrefs(context: Context) {
    private val sp = context.applicationContext
        .getSharedPreferences("parents_tv", Context.MODE_PRIVATE)

    var favorites: Set<String>
        get() = sp.getStringSet(KEY_FAVORITES, emptySet())?.toSet() ?: emptySet()
        set(value) {
            sp.edit().putStringSet(KEY_FAVORITES, value).apply()
        }

    var lastChannelId: String?
        get() = sp.getString(KEY_LAST_CHANNEL, null)
        set(value) {
            sp.edit().apply {
                if (value == null) remove(KEY_LAST_CHANNEL) else putString(KEY_LAST_CHANNEL, value)
            }.apply()
        }

    var lastCategoryId: String
        get() = sp.getString(KEY_LAST_CATEGORY, "cctv") ?: "cctv"
        set(value) {
            sp.edit().putString(KEY_LAST_CATEGORY, value).apply()
        }

    var zapWithUpDown: Boolean
        get() = sp.getBoolean(KEY_ZAP_UP_DOWN, true)
        set(value) {
            sp.edit().putBoolean(KEY_ZAP_UP_DOWN, value).apply()
        }

    var menuKeyAction: MenuKeyAction
        get() = MenuKeyAction.from(sp.getString(KEY_MENU_ACTION, null))
        set(value) {
            sp.edit().putString(KEY_MENU_ACTION, value.name).apply()
        }

    var backKeyAction: BackKeyAction
        get() = BackKeyAction.from(sp.getString(KEY_BACK_ACTION, null))
        set(value) {
            sp.edit().putString(KEY_BACK_ACTION, value.name).apply()
        }

    fun subgroupFor(categoryId: String): String? =
        sp.getString("subgroup_$categoryId", null)

    fun setSubgroup(categoryId: String, subgroup: String) {
        sp.edit().putString("subgroup_$categoryId", subgroup).apply()
    }

    fun lineFor(channelId: String): String? =
        sp.getString("$KEY_LINE_PREFIX$channelId", null)

    fun setLine(channelId: String, url: String) {
        sp.edit().putString("$KEY_LINE_PREFIX$channelId", url).apply()
    }

    fun clearFavorites() {
        sp.edit().remove(KEY_FAVORITES).apply()
    }

    private companion object {
        const val KEY_FAVORITES = "favorites"
        const val KEY_LAST_CHANNEL = "last_channel"
        const val KEY_LAST_CATEGORY = "last_category"
        const val KEY_ZAP_UP_DOWN = "zap_up_down"
        const val KEY_MENU_ACTION = "menu_action"
        const val KEY_BACK_ACTION = "back_action"
        const val KEY_LINE_PREFIX = "line_"
    }
}
