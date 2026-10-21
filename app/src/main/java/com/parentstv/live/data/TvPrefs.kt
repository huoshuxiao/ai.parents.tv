package com.parentstv.live.data

import android.content.Context

enum class MenuKeyAction {
    Lines,
    Aspect,
    Off;

    companion object {
        fun from(value: String?): MenuKeyAction =
            values().firstOrNull { it.name == value } ?: Lines
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

/**
 * 方向键的行为。用户要求上下键分开配置，而不是一个总开关：
 * 有人习惯上键翻到上一个台，也有人只想要焦点移动。
 */
enum class DirectionKeyAction {
    Zap,
    MoveFocus;

    companion object {
        fun from(value: String?): DirectionKeyAction =
            values().firstOrNull { it.name == value } ?: Zap
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

    var upKeyAction: DirectionKeyAction
        get() = DirectionKeyAction.from(sp.getString(KEY_UP_ACTION, null))
        set(value) {
            sp.edit().putString(KEY_UP_ACTION, value.name).apply()
        }

    var downKeyAction: DirectionKeyAction
        get() = DirectionKeyAction.from(sp.getString(KEY_DOWN_ACTION, null))
        set(value) {
            sp.edit().putString(KEY_DOWN_ACTION, value.name).apply()
        }

    var rememberLastChannel: Boolean
        get() = sp.getBoolean(KEY_REMEMBER_LAST, true)
        set(value) {
            sp.edit().putBoolean(KEY_REMEMBER_LAST, value).apply()
        }

    var epgEnabled: Boolean
        get() = sp.getBoolean(KEY_EPG_ENABLED, true)
        set(value) {
            sp.edit().putBoolean(KEY_EPG_ENABLED, value).apply()
        }

    var showVideoResolution: Boolean
        get() = sp.getBoolean(KEY_SHOW_RESOLUTION, true)
        set(value) {
            sp.edit().putBoolean(KEY_SHOW_RESOLUTION, value).apply()
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
        const val KEY_UP_ACTION = "up_key_action"
        const val KEY_DOWN_ACTION = "down_key_action"
        const val KEY_REMEMBER_LAST = "remember_last_channel"
        const val KEY_EPG_ENABLED = "epg_enabled"
        const val KEY_SHOW_RESOLUTION = "show_resolution"
        const val KEY_MENU_ACTION = "menu_action"
        const val KEY_BACK_ACTION = "back_action"
        const val KEY_LINE_PREFIX = "line_"
    }
}
