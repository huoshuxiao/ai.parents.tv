package com.parentstv.live.data

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test
import java.util.TimeZone

class BeijingTimeTest {
    private lateinit var originalZone: TimeZone

    @Before
    fun setUp() {
        originalZone = TimeZone.getDefault()
        TimeZone.setDefault(TimeZone.getTimeZone("UTC"))
    }

    @After
    fun tearDown() {
        TimeZone.setDefault(originalZone)
    }

    @Test
    fun epochIsFormattedAsBeijingTimeRegardlessOfDeviceZone() {
        assertEquals("1970-01-01 08:00", BeijingTime.format(0L, "yyyy-MM-dd HH:mm"))
    }

    @Test
    fun catalogUtcTimestampIsConvertedToBeijingTime() {
        assertEquals(
            "2026-10-21 13:17",
            BeijingTime.formatIso("2026-10-21T05:17:58Z", "yyyy-MM-dd HH:mm"),
        )
    }

    @Test
    fun fractionalSecondsAndDateRolloverAreHandled() {
        assertEquals(
            "2026-10-22 01:00",
            BeijingTime.formatIso("2026-10-21T17:00:00.250Z", "yyyy-MM-dd HH:mm"),
        )
    }

    @Test
    fun explicitOffsetsAreHandled() {
        assertEquals(
            "2026-10-21 13:17",
            BeijingTime.formatIso("2026-10-21T13:17:58+08:00", "yyyy-MM-dd HH:mm"),
        )
        assertEquals(
            "2026-10-22 10:30",
            BeijingTime.formatIso("2026-10-21T21:30:00-05:00", "yyyy-MM-dd HH:mm"),
        )
    }

    @Test
    fun malformedOrImpossibleTimestampsReturnNull() {
        assertNull(BeijingTime.formatIso("", "yyyy-MM-dd HH:mm"))
        assertNull(BeijingTime.formatIso("2026-10-21 05:17:58", "yyyy-MM-dd HH:mm"))
        assertNull(BeijingTime.formatIso("2026-02-30T05:17:58Z", "yyyy-MM-dd HH:mm"))
    }
}
